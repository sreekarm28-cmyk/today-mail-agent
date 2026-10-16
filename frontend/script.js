const CATEGORY_NAMES = ["Urgent Action", "Action Needed", "Important Info", "Updates", "Promotions"];
const CATEGORY_ICONS = {
  "Urgent Action": "!",
  "Action Needed": "↗",
  "Important Info": "✳",
  Updates: "◷",
  Promotions: "%",
};

const categoryNav = document.querySelector("#category-nav");
const filterTabs = document.querySelector("#filter-tabs");
const emailList = document.querySelector("#email-list");
const emptyState = document.querySelector("#empty-state");
const loadingState = document.querySelector("#loading-state");
const loadError = document.querySelector("#load-error");
const syncStatus = document.querySelector("#sync-status");
const connectButton = document.querySelector("#connect-gmail");
const gmailStatus = document.querySelector("#gmail-status");
const connectionNote = document.querySelector("#connection-note");
const emailDialog = document.querySelector("#email-dialog");
const summaryDialog = document.querySelector("#summary-dialog");
let emails = [];
let selectedEmail = null;
let activeFilter = "All mail";
let supabaseClient = null;
let currentSession = null;
let activeUserId = null;
let dashboardLoadedUserId = null;
let gmailConnected = false;
let syncInFlight = false;
let lastSyncAt = 0;
let autoSyncTimer = null;
const AUTO_SYNC_INTERVAL_MS = 5 * 60 * 1000;

async function initAppAuth() {
  const authScreen = document.querySelector("#auth-screen");
  const appShell = document.querySelector(".app-shell");
  const authForm = document.querySelector("#auth-form");
  const authError = document.querySelector("#auth-error");
  const authSubmit = document.querySelector("#auth-submit");
  const authToggle = document.querySelector("#auth-toggle");
  const signOutButton = document.querySelector("#sign-out");
  let isSignUp = false;

  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    if (!response.ok) throw new Error(config.detail || "Could not load app settings.");
    if (!config.supabase_url || !config.supabase_anon_key) return;
    if (!window.supabase?.createClient) throw new Error("Sign-in library did not load. Refresh the page and try again.");
    supabaseClient = window.supabase.createClient(config.supabase_url, config.supabase_anon_key);
    authScreen.hidden = false;
    appShell.hidden = true;

    const updateSession = (session) => {
      currentSession = session;
      const nextUserId = session?.user?.id || null;
      if (activeUserId !== nextUserId) {
        emails = [];
        activeFilter = "All mail";
        activeUserId = nextUserId;
        dashboardLoadedUserId = null;
        gmailConnected = false;
        lastSyncAt = 0;
        renderDashboard();
      }
      const signedIn = Boolean(session);
      authScreen.hidden = signedIn;
      appShell.hidden = !signedIn;
      signOutButton.hidden = !signedIn;
      if (signedIn) {
        gmailStatus.textContent = "Checking Gmail connection…";
        connectionNote.textContent = "Signed in";
        connectButton.textContent = "Connect Gmail ↗";
        connectButton.classList.remove("connected");
        connectButton.disabled = false;
        document.querySelector("#empty-connect").disabled = false;
        // Load saved inbox data while checking the Gmail connection status.
        void startDashboard(nextUserId);
      } else {
        gmailStatus.textContent = "Connect Gmail to get started";
        connectionNote.textContent = "Gmail not connected";
        connectButton.textContent = "Connect Gmail ↗";
        connectButton.classList.remove("connected");
        connectButton.disabled = false;
        gmailConnected = false;
        document.querySelector("#empty-connect").disabled = false;
      }
    };

    const { data } = await supabaseClient.auth.getSession();
    updateSession(data.session);
    supabaseClient.auth.onAuthStateChange((_event, session) => updateSession(session));

    authToggle.addEventListener("click", () => {
      isSignUp = !isSignUp;
      authSubmit.textContent = isSignUp ? "Create account" : "Sign in";
      authToggle.textContent = isSignUp ? "Already have an account? Sign in" : "Create a test account";
      document.querySelector("#auth-password").autocomplete = isSignUp ? "new-password" : "current-password";
      authError.textContent = "";
    });
    authForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      authSubmit.disabled = true;
      authError.textContent = "";
      const email = document.querySelector("#auth-email").value.trim();
      const password = document.querySelector("#auth-password").value;
      try {
        const result = isSignUp
          ? await supabaseClient.auth.signUp({ email, password })
          : await supabaseClient.auth.signInWithPassword({ email, password });
        if (result.error) throw result.error;
        if (isSignUp && !result.data.session) {
          authError.textContent = "Check your email to confirm your account, then sign in.";
        }
      } catch (error) {
        authError.textContent = error.message || "Could not sign in.";
      } finally {
        authSubmit.disabled = false;
      }
    });
    signOutButton.addEventListener("click", async () => {
      await supabaseClient.auth.signOut();
      authForm.reset();
    });
  } catch (error) {
    authScreen.hidden = false;
    appShell.hidden = true;
    authError.textContent = error.message || "Could not initialize sign-in.";
  }
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (currentSession?.access_token) headers.set("Authorization", `Bearer ${currentSession.access_token}`);
  return fetch(path, { ...options, headers });
}

async function updateGmailStatus() {
  if (!currentSession) return;
  try {
    const response = await apiFetch("/api/gmail/status");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not check Gmail connection.");
    if (payload.connected) {
      gmailConnected = true;
      setConnected(payload.email);
      document.querySelector("#empty-connect").hidden = true;
      if (new URLSearchParams(window.location.search).get("gmail") === "connected") {
        window.history.replaceState({}, "", window.location.pathname);
      }
    } else {
      gmailConnected = false;
      gmailStatus.textContent = "Gmail is not connected";
      connectionNote.textContent = "Gmail not connected";
      connectButton.textContent = "Connect Gmail ↗";
      connectButton.classList.remove("connected");
      document.querySelector("#empty-connect").hidden = false;
    }
  } catch (error) {
    gmailStatus.textContent = error.message || "Could not check Gmail connection.";
    gmailConnected = false;
  }
}

document.querySelector("#current-date").textContent = new Intl.DateTimeFormat("en", {
  weekday: "long", month: "long", day: "numeric",
}).format(new Date());

function setConnected(email) {
  gmailStatus.textContent = `Connected as ${email}`;
  connectionNote.textContent = "Gmail connected";
  connectButton.textContent = "Gmail connected ✓";
  connectButton.classList.add("connected");
}

async function connectGmail() {
  connectButton.disabled = true;
  connectButton.textContent = "Opening Google…";
  gmailStatus.textContent = "Approve Gmail access in the Google window.";
  try {
    const response = await apiFetch("/api/connect-gmail", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not connect Gmail.");
    if (payload.authorization_url) {
      window.location.assign(payload.authorization_url);
      return;
    }
    setConnected(payload.email);
    gmailConnected = true;
    dashboardLoadedUserId = null;
    await startDashboard(currentSession?.user?.id || "local-user");
  } catch (error) {
    gmailStatus.textContent = error.message || "Could not connect Gmail.";
    connectionNote.textContent = "Gmail not connected";
    connectButton.textContent = "Connect Gmail ↗";
    connectButton.classList.remove("connected");
    connectButton.disabled = false;
    showError(error.message || "Could not connect Gmail.");
  }
}

async function loadDatabaseEmails() {
  const response = await apiFetch("/api/dashboard/emails");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Could not load saved emails.");
  emails = payload.emails || [];
  renderDashboard();
  loadingState.hidden = true;
}

async function syncEmails() {
  if (syncInFlight || !gmailConnected) return;
  syncInFlight = true;
  loadError.hidden = true;
  syncStatus.hidden = false;
  syncStatus.className = "sync-status";
  syncStatus.textContent = emails.length ? "Refreshing saved inbox…" : "Loading your saved inbox…";
  if (!emails.length) {
    loadingState.hidden = false;
    emptyState.hidden = true;
  }
  try {
    const response = await apiFetch("/api/dashboard/sync", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not refresh your saved inbox.");
    emails = payload.emails || [];
    renderDashboard();
    lastSyncAt = Date.now();
    syncStatus.textContent = "Saved inbox updated.";
    gmailStatus.textContent = "Gmail connected";
    connectionNote.textContent = "Gmail connected";
  } catch (error) {
    syncStatus.classList.add("has-error");
    syncStatus.textContent = emails.length
      ? "Could not refresh. Showing your saved emails."
      : (error.message || "Could not load your saved emails.");
    if (!emails.length) {
      showError(error.message || "Could not load your emails.");
      renderDashboard();
    }
  } finally {
    loadingState.hidden = true;
    syncInFlight = false;
  }
}

async function startDashboard(userId) {
  if (dashboardLoadedUserId === userId) return;
  dashboardLoadedUserId = userId;
  loadingState.hidden = false;
  emptyState.hidden = true;
  syncStatus.hidden = false;
  syncStatus.textContent = "Loading saved emails…";
  const databaseLoad = loadDatabaseEmails().catch(() => {
    emails = [];
  });
  const gmailStatusCheck = updateGmailStatus();
  await Promise.all([databaseLoad, gmailStatusCheck]);
  syncStatus.textContent = emails.length
    ? "Showing your saved inbox…"
    : "Loading your mailbox…";
  if (gmailConnected) await syncEmails();
  else loadingState.hidden = true;
  if (!autoSyncTimer) {
    autoSyncTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void syncEmails();
    }, AUTO_SYNC_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && Date.now() - lastSyncAt > 60_000) {
        void syncEmails();
      }
    });
  }
}

function showError(message) {
  loadError.textContent = message;
  loadError.hidden = false;
}

function matchingCategory(email) {
  return CATEGORY_NAMES.find((name) => (email.labels || []).includes(name)) || "Unsorted";
}

function renderFilters() {
  const items = ["All mail", ...CATEGORY_NAMES, "Unsorted"];
  filterTabs.replaceChildren();
  categoryNav.replaceChildren();
  for (const name of items) {
    const count = name === "All mail"
      ? emails.length
      : emails.filter((email) => matchingCategory(email) === name).length;
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = `filter-tab${activeFilter === name ? " active" : ""}`;
    tab.setAttribute("aria-pressed", String(activeFilter === name));
    const icon = document.createElement("span");
    icon.className = `category-icon ${categoryClass(name)}`;
    icon.textContent = name === "All mail" ? "▤" : CATEGORY_ICONS[name] || "·";
    const title = document.createElement("span");
    title.textContent = name;
    const badge = document.createElement("span");
    badge.className = "filter-count";
    badge.textContent = String(count);
    tab.append(icon, title, badge);
    tab.addEventListener("click", () => {
      activeFilter = name;
      renderDashboard();
    });
    filterTabs.append(tab);

    if (name !== "All mail") {
      const navItem = document.createElement("button");
      navItem.type = "button";
      navItem.className = `nav-item category-nav-item${activeFilter === name ? " selected" : ""}`;
      const navIcon = document.createElement("span");
      navIcon.className = `nav-symbol ${categoryClass(name)}`;
      navIcon.textContent = CATEGORY_ICONS[name] || "·";
      const navTitle = document.createElement("span");
      navTitle.textContent = name;
      const navCount = document.createElement("span");
      navCount.className = "nav-count";
      navCount.textContent = String(count);
      navItem.append(navIcon, navTitle, navCount);
      navItem.addEventListener("click", () => {
        activeFilter = name;
        renderDashboard();
      });
      categoryNav.append(navItem);
    }
  }
}

function categoryClass(name) {
  return name.toLowerCase().replaceAll(" ", "-");
}

function formatReceived(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "";
  return new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" }).format(date);
}

function formatFullReceived(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function initials(value) {
  const name = (value || "?").replace(/<[^>]+>/g, "").trim();
  return name.split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase() || "?";
}

function renderEmail(email) {
  const card = document.createElement("article");
  card.className = "email-card";
  card.dataset.emailId = email.id;
  const avatar = document.createElement("div");
  avatar.className = "sender-avatar";
  avatar.textContent = initials(email.from);

  const content = document.createElement("div");
  content.className = "email-main";
  const top = document.createElement("div");
  top.className = "email-meta";
  const sender = document.createElement("span");
  sender.className = "email-sender";
  sender.textContent = email.from || "Unknown sender";
  const time = document.createElement("time");
  time.className = "email-time";
  time.dateTime = email.received || "";
  time.textContent = formatReceived(email.received);
  top.append(sender, time);
  const subject = document.createElement("h3");
  subject.className = "email-subject";
  subject.textContent = email.subject || "(No subject)";
  const preview = document.createElement("p");
  preview.className = "email-preview";
  preview.textContent = email.preview || "No preview available.";
  const openButton = document.createElement("button");
  openButton.type = "button";
  openButton.className = "email-open-button";
  openButton.setAttribute("aria-label", `Open email: ${email.subject || "No subject"}`);
  openButton.append(top, subject, preview);
  openButton.addEventListener("click", () => openEmail(email));
  const footer = document.createElement("div");
  footer.className = "email-card-footer";
  const labels = document.createElement("div");
  labels.className = "email-labels";
  const category = matchingCategory(email);
  const chip = document.createElement("span");
  chip.className = `label-chip ${categoryClass(category)}`;
  chip.textContent = category;
  labels.append(chip);
  for (const label of (email.labels || []).filter((name) => name !== category)) {
    const extra = document.createElement("span");
    extra.className = "label-chip extra-label";
    extra.textContent = label;
    labels.append(extra);
  }
  const summarize = document.createElement("button");
  summarize.type = "button";
  summarize.className = "summarize-button";
  summarize.innerHTML = '<span aria-hidden="true">✳</span> Summarize';
  summarize.addEventListener("click", () => summarizeEmail(email, summarize));
  footer.append(labels, summarize);
  content.append(openButton, footer);
  card.append(avatar, content);
  card.dataset.renderKey = JSON.stringify([
    email.from, email.subject, email.received, email.preview, email.labels,
  ]);
  return card;
}

function renderDashboard() {
  renderFilters();
  document.querySelector("#email-count").textContent = String(emails.length);
  document.querySelector("#sidebar-count").textContent = String(emails.length);
  document.querySelector("#summary-title").textContent = emails.length
    ? `${emails.length} ${emails.length === 1 ? "email" : "emails"} in your recent inbox`
    : "Your recent mail, in one place";
  document.querySelector("#summary-subtitle").textContent = emails.length
    ? "Use a category to narrow the list, or summarize one email."
    : "Connect Gmail to load your recent inbox automatically.";
  const shown = activeFilter === "All mail"
    ? emails
    : emails.filter((email) => matchingCategory(email) === activeFilter);
  const existingCards = new Map(
    [...emailList.children].map((card) => [card.dataset.emailId, card]),
  );
  const nextCards = shown.map((email) => {
    const renderKey = JSON.stringify([
      email.from, email.subject, email.received, email.preview, email.labels,
    ]);
    const existing = existingCards.get(email.id);
    if (existing?.dataset.renderKey === renderKey) return existing;
    const card = renderEmail(email);
    card.dataset.renderKey = renderKey;
    return card;
  });
  emailList.replaceChildren(...nextCards);
  emailList.hidden = shown.length === 0;
  emptyState.hidden = shown.length > 0;
  if (emails.length && !shown.length) {
    emptyState.hidden = false;
    emptyState.querySelector("h3").textContent = `No emails in ${activeFilter}`;
    emptyState.querySelector("p").textContent = "Emails will appear here when they have this category.";
    document.querySelector("#empty-connect").hidden = true;
  } else {
    emptyState.querySelector("h3").textContent = "Your inbox will show up here";
    emptyState.querySelector("p").textContent = "Connect Gmail to save your recent messages in your mailbox.";
    document.querySelector("#empty-connect").hidden = false;
  }
}

async function openEmail(email) {
  selectedEmail = email;
  document.querySelector("#email-dialog-subject").textContent = email.subject || "Email";
  document.querySelector("#email-dialog-from").textContent = email.from || "Unknown sender";
  document.querySelector("#email-dialog-to").textContent = "";
  document.querySelector("#email-dialog-cc").textContent = "";
  document.querySelector("#email-dialog-to-row").hidden = true;
  document.querySelector("#email-dialog-cc-row").hidden = true;
  document.querySelector("#email-dialog-received").textContent = formatFullReceived(email.received);
  const body = document.querySelector("#email-dialog-body");
  body.textContent = "Loading the saved email…";
  if (!emailDialog.open) emailDialog.showModal();

  try {
    const response = await apiFetch(`/api/dashboard/emails/${encodeURIComponent(email.id)}`);
    const responseText = await response.text();
    let payload;
    try {
      payload = responseText ? JSON.parse(responseText) : {};
    } catch {
      throw new Error(responseText.trim() || `Server returned HTTP ${response.status}.`);
    }
    if (!response.ok) throw new Error(payload.detail || "Could not open this email.");
    document.querySelector("#email-dialog-subject").textContent = payload.subject || "(No subject)";
    document.querySelector("#email-dialog-from").textContent = payload.from || "Unknown sender";
    document.querySelector("#email-dialog-to").textContent = payload.to || "";
    document.querySelector("#email-dialog-to-row").hidden = !payload.to;
    document.querySelector("#email-dialog-cc").textContent = payload.cc || "";
    document.querySelector("#email-dialog-cc-row").hidden = !payload.cc;
    document.querySelector("#email-dialog-received").textContent = formatFullReceived(payload.received);
    body.textContent = payload.body || "(No readable text body available.)";
  } catch (error) {
    body.textContent = error.message || "Could not open this email.";
  }
}

async function hideEmailFromApp() {
  if (!selectedEmail) return;
  const emailToHide = selectedEmail;
  const button = document.querySelector("#hide-email-button");
  if (!window.confirm("Remove this email from this app? It will remain in Gmail.")) return;

  button.disabled = true;
  button.textContent = "Removing…";
  try {
    const response = await apiFetch(`/api/dashboard/emails/${encodeURIComponent(emailToHide.id)}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not remove this email.");
    emails = emails.filter((email) => email.id !== emailToHide.id);
    selectedEmail = null;
    emailDialog.close();
    renderDashboard();
    syncStatus.hidden = false;
    syncStatus.className = "sync-status";
    syncStatus.textContent = "Email removed from this app. It remains in Gmail.";
  } catch (error) {
    showError(error.message || "Could not remove this email.");
  } finally {
    button.disabled = false;
    button.textContent = "Remove from app";
  }
}

async function summarizeEmail(email, button) {
  const originalText = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<span class="button-spinner"></span> Summarizing';
  document.querySelector("#dialog-subject").textContent = email.subject || "Email summary";
  document.querySelector("#dialog-summary").textContent = "Reading this email…";
  if (!summaryDialog.open) summaryDialog.showModal();
  try {
    const response = await apiFetch(`/api/dashboard/emails/${encodeURIComponent(email.id)}/summary`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not summarize this email.");
    document.querySelector("#dialog-summary").textContent = payload.summary || "No summary was returned.";
  } catch (error) {
    document.querySelector("#dialog-summary").textContent = error.message || "Could not summarize this email.";
  } finally {
    button.disabled = false;
    button.innerHTML = originalText;
  }
}

connectButton.addEventListener("click", connectGmail);
document.querySelector("#empty-connect").addEventListener("click", connectGmail);
document.querySelector("#close-email-dialog").addEventListener("click", () => emailDialog.close());
document.querySelector("#hide-email-button").addEventListener("click", hideEmailFromApp);
emailDialog.addEventListener("click", (event) => {
  if (event.target === emailDialog) emailDialog.close();
});
document.querySelector("#close-dialog").addEventListener("click", () => summaryDialog.close());
summaryDialog.addEventListener("click", (event) => {
  if (event.target === summaryDialog) summaryDialog.close();
});

renderDashboard();
initAppAuth();
