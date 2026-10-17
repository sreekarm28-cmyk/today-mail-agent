const CATEGORY_NAMES = ["Urgent Action", "Action Needed", "Important Info", "Updates", "Promotions"];
const CATEGORY_ICONS = {
  "Urgent Action": "!",
  "Action Needed": "↗",
  "Important Info": "✳",
  Updates: "◷",
  Promotions: "%",
};

const categoryNav = document.querySelector("#category-nav");
const accountFilters = document.querySelector("#account-filters");
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
let gmailAccounts = [];
let selectedEmail = null;
let activeFilter = "All mail";
let activeAccountFilter = "all";
let supabaseClient = null;
let currentSession = null;
let activeUserId = null;
let dashboardLoadedUserId = null;
let gmailConnected = false;
let gmailStatusChecked = false;
let syncInFlight = false;
let lastSyncAt = 0;
let autoSyncTimer = null;
const MAX_GMAIL_ACCOUNTS = 2;
const AUTO_SYNC_INTERVAL_MS = 30 * 1000;

function accountFilterStorageKey(userId = activeUserId) {
  return userId ? `today-mail-account-filter:${userId}` : "";
}

function rememberAccountFilter() {
  const key = accountFilterStorageKey();
  if (!key) return;
  try {
    window.localStorage.setItem(key, activeAccountFilter);
  } catch {
    // The filter still works for this page even if browser storage is unavailable.
  }
}

function restoreAccountFilter(userId) {
  activeAccountFilter = "all";
  if (!userId) return;
  try {
    activeAccountFilter = window.localStorage.getItem(accountFilterStorageKey(userId)) || "all";
  } catch {
    activeAccountFilter = "all";
  }
}

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
      const userChanged = activeUserId !== nextUserId;
      if (userChanged) {
        emails = [];
        gmailAccounts = [];
        activeFilter = "All mail";
        activeUserId = nextUserId;
        restoreAccountFilter(nextUserId);
        dashboardLoadedUserId = null;
        gmailConnected = false;
        gmailStatusChecked = false;
        lastSyncAt = 0;
        renderDashboard();
      }
      const signedIn = Boolean(session);
      authScreen.hidden = signedIn;
      appShell.hidden = !signedIn;
      signOutButton.hidden = !signedIn;
      if (signedIn) {
        if (userChanged) {
          gmailStatus.textContent = "Checking Gmail connection…";
          connectionNote.textContent = "Signed in";
          connectButton.textContent = "Connect Gmail ↗";
          connectButton.classList.remove("connected");
          connectButton.disabled = false;
        }
        document.querySelector("#empty-connect").disabled = false;
        // Load saved inbox data while checking the Gmail connection status.
        if (userChanged || dashboardLoadedUserId !== nextUserId) void startDashboard(nextUserId);
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
    gmailAccounts = Array.isArray(payload.accounts) ? payload.accounts : (
      payload.connected && payload.email
        ? [{ id: "legacy", email: payload.email }]
        : []
    );
    const connected = gmailAccounts.length > 0 || Boolean(payload.connected);
    if (connected) {
      gmailConnected = true;
      const accountNames = gmailAccounts.map((account) => account.email).filter(Boolean);
      const statusText = accountNames.length
        ? `${accountNames.length} Gmail ${accountNames.length === 1 ? "account" : "accounts"} connected`
        : `Connected as ${payload.email}`;
      setConnected(statusText);
      if (activeAccountFilter !== "all" && !gmailAccounts.some((account) => account.id === activeAccountFilter)) {
        activeAccountFilter = "all";
        rememberAccountFilter();
      }
      document.querySelector("#empty-connect").hidden = true;
      if (new URLSearchParams(window.location.search).get("gmail") === "connected") {
        window.history.replaceState({}, "", window.location.pathname);
      }
    } else {
      gmailAccounts = [];
      gmailConnected = false;
      gmailStatus.textContent = "Gmail is not connected";
      connectionNote.textContent = "Gmail not connected";
      connectButton.textContent = "Connect Gmail ↗";
      connectButton.classList.remove("connected");
      document.querySelector("#empty-connect").hidden = false;
    }
    gmailStatusChecked = true;
    if (new URLSearchParams(window.location.search).get("gmail") === "limit") {
      showError("You can connect a maximum of two Gmail accounts to this app.");
      window.history.replaceState({}, "", window.location.pathname);
    }
    renderDashboard();
  } catch (error) {
    gmailStatus.textContent = error.message || "Could not check Gmail connection.";
    gmailConnected = false;
    gmailAccounts = [];
    gmailStatusChecked = true;
    connectionNote.textContent = "Gmail status unavailable";
    renderDashboard();
  }
}

document.querySelector("#current-date").textContent = new Intl.DateTimeFormat("en", {
  weekday: "long", month: "long", day: "numeric",
}).format(new Date());

function setConnected(email) {
  gmailStatusChecked = true;
  gmailStatus.textContent = email.startsWith("Connected as ") || email.includes("accounts connected")
    ? email
    : `Connected as ${email}`;
  connectionNote.textContent = "Gmail connected";
  connectButton.textContent = gmailAccounts.length >= MAX_GMAIL_ACCOUNTS
    ? "2 accounts connected"
    : "Add another Gmail ↗";
  connectButton.classList.add("connected");
}

async function disconnectGmailAccount(account, button) {
  const confirmed = window.confirm(
    `Disconnect ${account.email}? Its saved emails and sync data will be removed from this app. The messages will remain in Gmail.`,
  );
  if (!confirmed) return;

  button.disabled = true;
  button.textContent = "…";
  try {
    const response = await apiFetch(`/api/gmail/accounts/${encodeURIComponent(account.id)}`, {
      method: "DELETE",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not disconnect Gmail.");

    gmailAccounts = gmailAccounts.filter((item) => item.id !== account.id);
    emails = emails.filter((email) => email.connection_id !== account.id);
    if (activeAccountFilter === account.id) activeAccountFilter = "all";
    rememberAccountFilter();
    gmailConnected = gmailAccounts.length > 0;
    if (gmailConnected) {
      const names = gmailAccounts.map((item) => item.email).filter(Boolean);
      setConnected(`${names.length} Gmail ${names.length === 1 ? "account" : "accounts"} connected`);
    } else {
      gmailStatus.textContent = "Gmail is not connected";
      connectionNote.textContent = "Gmail not connected";
      connectButton.textContent = "Connect Gmail ↗";
      connectButton.classList.remove("connected");
      document.querySelector("#empty-connect").hidden = false;
    }
    syncStatus.hidden = false;
    syncStatus.className = "sync-status";
    syncStatus.textContent = "Account disconnected. Its saved emails were removed from this app; Gmail is unchanged.";
    renderDashboard();
  } catch (error) {
    showError(error.message || "Could not disconnect Gmail.");
    renderAccountFilters();
  }
}

async function connectGmail() {
  if (gmailAccounts.length >= MAX_GMAIL_ACCOUNTS) {
    showError("You can connect up to two Gmail accounts.");
    return;
  }
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
    renderDashboard();
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
  if (Array.isArray(payload.accounts)) gmailAccounts = payload.accounts;
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
    if (Array.isArray(payload.accounts)) gmailAccounts = payload.accounts;
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
      if (document.visibilityState === "visible" && Date.now() - lastSyncAt > AUTO_SYNC_INTERVAL_MS) {
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

function emailsForSelectedAccount() {
  if (activeAccountFilter === "all") return emails;
  return emails.filter((email) => email.connection_id === activeAccountFilter);
}

function renderAccountFilters() {
  accountFilters.replaceChildren();
  if (!gmailAccounts.length) {
    accountFilters.hidden = true;
    return;
  }
  accountFilters.hidden = false;
  const options = [
    { id: "all", email: "All accounts", all: true },
    ...gmailAccounts.map((account) => ({
      id: account.id,
      email: account.email,
      all: false,
    })),
  ];
  for (const account of options) {
    const item = document.createElement("div");
    item.className = "account-filter-item";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `account-filter${account.all ? " all-accounts" : ""}${activeAccountFilter === account.id ? " active" : ""}`;
    button.setAttribute("aria-pressed", String(activeAccountFilter === account.id));
    const dot = document.createElement("span");
    dot.className = "account-filter-dot";
    const label = document.createElement("span");
    label.textContent = account.email;
    button.append(dot, label);
    button.addEventListener("click", () => {
      activeAccountFilter = account.id;
      rememberAccountFilter();
      renderDashboard();
    });
    item.append(button);
    if (!account.all) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "account-remove-button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Disconnect ${account.email}`);
      remove.title = `Disconnect ${account.email}`;
      remove.addEventListener("click", () => void disconnectGmailAccount(account, remove));
      item.append(remove);
    }
    accountFilters.append(item);
  }
}

function renderFilters() {
  const items = ["All mail", ...CATEGORY_NAMES, "Unsorted"];
  const accountEmails = emailsForSelectedAccount();
  filterTabs.replaceChildren();
  categoryNav.replaceChildren();
  for (const name of items) {
    const count = name === "All mail"
      ? accountEmails.length
      : accountEmails.filter((email) => matchingCategory(email) === name).length;
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
  if (activeAccountFilter === "all") {
    const accountEmail = email.account_email
      || gmailAccounts.find((account) => account.id === email.connection_id)?.email;
    if (accountEmail) {
      const accountTag = document.createElement("span");
      accountTag.className = "account-tag";
      accountTag.textContent = accountEmail;
      accountTag.title = accountEmail;
      labels.append(accountTag);
    }
  }
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
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-email-button";
  remove.textContent = "Remove";
  remove.setAttribute("aria-label", `Remove email from app: ${email.subject || "No subject"}`);
  remove.addEventListener("click", () => hideEmailFromApp(email, remove));
  footer.append(labels, summarize, remove);
  content.append(openButton, footer);
  card.append(avatar, content);
  card.dataset.renderKey = JSON.stringify([
    email.from, email.subject, email.received, email.preview, email.labels,
    email.connection_id, email.account_email, activeAccountFilter === "all",
  ]);
  return card;
}

function renderDashboard() {
  renderAccountFilters();
  renderFilters();
  const accountEmails = emailsForSelectedAccount();
  document.querySelector("#email-count").textContent = String(accountEmails.length);
  document.querySelector("#sidebar-count").textContent = String(accountEmails.length);
  document.querySelector("#summary-title").textContent = accountEmails.length
    ? `${accountEmails.length} ${accountEmails.length === 1 ? "email" : "emails"} in your recent inbox`
    : "Your recent mail, in one place";
  document.querySelector("#summary-subtitle").textContent = emails.length
    ? "Use a category to narrow the list, or summarize one email."
    : !gmailStatusChecked
      ? "Checking your Gmail connection…"
      : gmailConnected
        ? "Gmail is connected. New messages will appear here after syncing."
        : "Connect Gmail to load your recent inbox automatically.";
  const shown = activeFilter === "All mail"
    ? accountEmails
    : accountEmails.filter((email) => matchingCategory(email) === activeFilter);
  const existingCards = new Map(
    [...emailList.children].map((card) => [card.dataset.emailId, card]),
  );
  const nextCards = shown.map((email) => {
    const renderKey = JSON.stringify([
      email.from, email.subject, email.received, email.preview, email.labels,
      email.connection_id, email.account_email, activeAccountFilter === "all",
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
  if (accountEmails.length && !shown.length) {
    emptyState.hidden = false;
    emptyState.querySelector("h3").textContent = `No emails in ${activeFilter}`;
    emptyState.querySelector("p").textContent = "Emails will appear here when they match this category and account.";
    document.querySelector("#empty-connect").hidden = true;
  } else {
    emptyState.querySelector("h3").textContent = "Your inbox will show up here";
    emptyState.querySelector("p").textContent = !gmailStatusChecked
      ? "Checking your Gmail connection…"
      : gmailConnected
        ? "Gmail is connected. No messages have appeared in the last 24 hours yet."
        : "Connect Gmail to save your recent messages in your mailbox.";
    document.querySelector("#empty-connect").hidden = gmailConnected || !gmailStatusChecked;
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

async function hideEmailFromApp(email = selectedEmail, button = document.querySelector("#hide-email-button")) {
  if (!email) return;
  const emailToHide = email;
  const originalText = button.textContent;
  if (!window.confirm(`Remove “${emailToHide.subject || "this email"}” from this app? It will remain in Gmail.`)) return;

  button.disabled = true;
  button.textContent = "Removing…";
  try {
    const response = await apiFetch(`/api/dashboard/emails/${encodeURIComponent(emailToHide.id)}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not remove this email.");
    emails = emails.filter((email) => email.id !== emailToHide.id);
    if (selectedEmail?.id === emailToHide.id) {
      selectedEmail = null;
      emailDialog.close();
    }
    renderDashboard();
    syncStatus.hidden = false;
    syncStatus.className = "sync-status";
    syncStatus.textContent = "Email removed from this app. It remains in Gmail.";
  } catch (error) {
    showError(error.message || "Could not remove this email.");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
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
document.querySelector("#hide-email-button").addEventListener("click", () => hideEmailFromApp());
emailDialog.addEventListener("click", (event) => {
  if (event.target === emailDialog) emailDialog.close();
});
document.querySelector("#close-dialog").addEventListener("click", () => summaryDialog.close());
summaryDialog.addEventListener("click", (event) => {
  if (event.target === summaryDialog) summaryDialog.close();
});

renderDashboard();
initAppAuth();
