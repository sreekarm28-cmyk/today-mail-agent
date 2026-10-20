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
const inboxNav = document.querySelector("#inbox-nav");
const askAiNav = document.querySelector("#ask-ai-nav");
const remindersNav = document.querySelector("#reminders-nav");
const inboxView = document.querySelector("#inbox-view");
const askAiView = document.querySelector("#ask-ai-view");
const remindersView = document.querySelector("#reminders-view");
const chatAccountScope = document.querySelector("#chat-account-scope");
const pageTitle = document.querySelector("#page-title");
const pageContext = document.querySelector("#page-context");
const askAiThread = document.querySelector("#ask-ai-empty").parentElement;
const askAiComposer = document.querySelector("#ask-ai-composer");
const askAiInput = document.querySelector("#ask-ai-input");
const askAiSend = askAiComposer.querySelector('button[type="submit"]');
const gmailStatus = document.querySelector("#gmail-status");
const connectionNote = document.querySelector("#connection-note");
const emailDialog = document.querySelector("#email-dialog");
const summaryDialog = document.querySelector("#summary-dialog");
const composeDialog = document.querySelector("#compose-dialog");
const replyOpenButton = document.querySelector("#reply-open");
const composeStatus = document.querySelector("#compose-status");
const composeDraftArea = document.querySelector("#compose-draft-area");
const composeGenerateButton = document.querySelector("#compose-generate");
const composeSendButton = document.querySelector("#compose-send");
const composeScheduleControls = document.querySelector("#compose-schedule-controls");
const remindEmailButton = document.querySelector("#remind-email");
const reminderDialog = document.querySelector("#reminder-dialog");
const reminderForm = document.querySelector("#reminder-form");
const reminderAtInput = document.querySelector("#reminder-at");
const reminderNoteInput = document.querySelector("#reminder-note");
const reminderList = document.querySelector("#reminders-list");
const remindersStatus = document.querySelector("#reminders-status");
const reminderFormStatus = document.querySelector("#reminder-form-status");
let emails = [];
let gmailAccounts = [];
let reminders = [];
let selectedEmail = null;
let reminderEditingId = null;
let activeReminderTab = "upcoming";
let remindersLoaded = false;
let composeMode = "new";
let composeReplyEmail = null;
let askAiHistory = [];
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
        reminders = [];
        remindersLoaded = false;
        resetAskAiChat();
        showPage("inbox");
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
        resetAskAiChat();
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
    if (new URLSearchParams(window.location.search).get("gmail") === "send_enabled") {
      syncStatus.hidden = false;
      syncStatus.className = "sync-status";
      syncStatus.textContent = "Gmail sending permission is enabled. You can now send messages from this account.";
      window.history.replaceState({}, "", window.location.pathname);
    }
    if (new URLSearchParams(window.location.search).get("gmail") === "wrong_account") {
      showError("Choose the same Gmail account you are enabling replies for.");
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
    if (activeAccountFilter === account.id) {
      activeAccountFilter = "all";
      resetAskAiChat();
    }
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
  void loadReminders();
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
      if (document.visibilityState === "visible" && !remindersView.hidden && remindersLoaded) {
        renderReminders();
        void loadReminders();
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

function showPage(page) {
  const isAskAi = page === "ask-ai";
  const isReminders = page === "reminders";
  inboxView.hidden = isAskAi || isReminders;
  askAiView.hidden = !isAskAi;
  remindersView.hidden = !isReminders;
  inboxNav.classList.toggle("selected", !isAskAi && !isReminders);
  askAiNav.classList.toggle("selected", isAskAi);
  remindersNav.classList.toggle("selected", isReminders);
  inboxNav.toggleAttribute("aria-current", !isAskAi && !isReminders);
  askAiNav.toggleAttribute("aria-current", isAskAi);
  remindersNav.toggleAttribute("aria-current", isReminders);
  pageTitle.textContent = isAskAi ? "Ask AI" : isReminders ? "Reminders" : "Inbox";
  pageContext.hidden = isAskAi || isReminders;
  if (isAskAi) renderAskAiAccountScope();
  if (isReminders) void loadReminders();
}

window.setInterval(() => {
  if (document.visibilityState === "visible" && !remindersView.hidden && remindersLoaded) renderReminders();
}, 30 * 1000);

function setReminderStatus(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.hidden = !message;
}

async function reminderRequest(path, options = {}) {
  const response = await apiFetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch { /* Use a safe fallback below. */ }
  if (!response.ok) throw new Error(payload.detail || "Could not update reminders. Please try again.");
  return payload;
}

async function loadReminders() {
  if (!currentSession) return;
  setReminderStatus(remindersStatus, "Loading reminders…");
  try {
    const payload = await reminderRequest("/api/dashboard/reminders");
    reminders = Array.isArray(payload.reminders) ? payload.reminders : [];
    remindersLoaded = true;
    renderReminders();
    setReminderStatus(remindersStatus, "");
  } catch (error) {
    setReminderStatus(remindersStatus, error.message || "Could not load reminders.", true);
  }
}

function renderReminders() {
  const now = Date.now();
  const upcoming = reminders.filter((item) => new Date(item.remind_at).getTime() > now);
  const past = reminders.filter((item) => new Date(item.remind_at).getTime() <= now);
  document.querySelector("#upcoming-count").textContent = String(upcoming.length);
  document.querySelector("#past-count").textContent = String(past.length);
  const count = upcoming.length + past.length;
  const badge = document.querySelector("#reminders-count");
  badge.textContent = String(count);
  badge.hidden = count === 0;
  const shown = activeReminderTab === "upcoming" ? upcoming : past;
  reminderList.replaceChildren(...shown.map((item) => makeReminderCard(item, activeReminderTab === "past")));
  reminderList.hidden = shown.length === 0;
  document.querySelector("#reminders-empty").hidden = shown.length > 0;
}

function makeReminderCard(reminder, isPast) {
  const card = document.createElement("article");
  card.className = `reminder-card${isPast ? " is-past" : ""}`;
  const mark = document.createElement("span");
  mark.className = "reminder-card-mark";
  mark.textContent = isPast ? "!" : "◷";
  const content = document.createElement("div");
  content.className = "reminder-card-content";
  const date = document.createElement("div");
  date.className = "reminder-card-date";
  date.textContent = isPast
    ? `Due ${formatFullReceived(reminder.remind_at)}`
    : formatFullReceived(reminder.remind_at);
  const subject = document.createElement("h2");
  subject.textContent = reminder.subject || "Email no longer available";
  const sender = document.createElement("p");
  sender.className = "reminder-card-sender";
  sender.textContent = reminder.sender || "";
  content.append(date, subject, sender);
  if (reminder.note) {
    const note = document.createElement("p");
    note.className = "reminder-card-note";
    note.textContent = reminder.note;
    content.append(note);
  }
  const actions = document.createElement("div");
  actions.className = "reminder-card-actions";
  if (reminder.email_available) {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "reminder-action-link";
    open.textContent = "Open email";
    open.addEventListener("click", () => {
      const email = emails.find((item) => item.id === reminder.email_id) || {
        id: reminder.email_id,
        subject: reminder.subject,
        from: reminder.sender,
        received: reminder.received,
      };
      void openEmail(email);
    });
    actions.append(open);
  }
  if (isPast) {
    const again = document.createElement("button");
    again.type = "button";
    again.className = "reminder-action-link";
    again.textContent = "Remind me again";
    again.addEventListener("click", () => openReminderForm(null, reminder));
    actions.append(again);
  } else {
    const done = document.createElement("button");
    done.type = "button";
    done.className = "reminder-action-link";
    done.textContent = "Mark done";
    done.addEventListener("click", () => void completeReminder(reminder.id, done));
    actions.append(done);
    const change = document.createElement("button");
    change.type = "button";
    change.className = "reminder-action-link";
    change.textContent = "Change time";
    change.addEventListener("click", () => openReminderForm(null, reminder));
    actions.append(change);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "reminder-delete-button";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => void deleteReminder(reminder.id, remove));
  actions.append(remove);
  card.append(mark, content, actions);
  return card;
}

function toLocalDateTimeInput(value) {
  const date = value ? new Date(value) : new Date(Date.now() + 60 * 60 * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function openReminderForm(email = selectedEmail, existing = null) {
  if (!email && !existing) return;
  reminderEditingId = existing?.id || null;
  reminderForm.reset();
  reminderAtInput.min = toLocalDateTimeInput(new Date(Date.now() + 2 * 60 * 1000).toISOString());
  reminderAtInput.value = toLocalDateTimeInput(existing?.remind_at);
  reminderNoteInput.value = existing?.note || "";
  document.querySelector("#reminder-dialog-title").textContent = existing ? "Change reminder" : "Remind me about this email";
  document.querySelector("#save-reminder").textContent = existing ? "Save changes" : "Save reminder";
  document.querySelector("#reminder-email-subject").textContent = existing?.subject || email?.subject || "Selected email";
  reminderForm.dataset.emailId = existing?.email_id || email?.id || "";
  setReminderStatus(reminderFormStatus, "");
  if (!reminderDialog.open) reminderDialog.showModal();
}

async function submitReminder(event) {
  event.preventDefault();
  const localValue = reminderAtInput.value;
  if (!localValue) return;
  const remindAt = new Date(localValue);
  if (Number.isNaN(remindAt.getTime()) || remindAt.getTime() <= Date.now()) {
    setReminderStatus(reminderFormStatus, "Choose a future date and time.", true);
    return;
  }
  const button = document.querySelector("#save-reminder");
  button.disabled = true;
  setReminderStatus(reminderFormStatus, "Saving…");
  try {
    const body = { remind_at: remindAt.toISOString(), note: reminderNoteInput.value.trim() };
    if (reminderEditingId) {
      await reminderRequest(`/api/dashboard/reminders/${encodeURIComponent(reminderEditingId)}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
    } else {
      await reminderRequest("/api/dashboard/reminders", {
        method: "POST", body: JSON.stringify({ ...body, email_id: reminderForm.dataset.emailId }),
      });
    }
    reminderDialog.close();
    activeReminderTab = "upcoming";
    document.querySelectorAll(".reminder-tab").forEach((tab) => tab.classList.toggle("selected", tab.dataset.reminderTab === activeReminderTab));
    await loadReminders();
  } catch (error) {
    setReminderStatus(reminderFormStatus, error.message || "Could not save this reminder.", true);
  } finally {
    button.disabled = false;
  }
}

async function completeReminder(id, button) {
  button.disabled = true;
  try {
    await reminderRequest(`/api/dashboard/reminders/${encodeURIComponent(id)}/complete`, { method: "POST" });
    reminders = reminders.filter((item) => item.id !== id);
    renderReminders();
  } catch (error) {
    button.disabled = false;
    setReminderStatus(remindersStatus, error.message || "Could not complete reminder.", true);
  }
}

async function deleteReminder(id, button) {
  button.disabled = true;
  try {
    await reminderRequest(`/api/dashboard/reminders/${encodeURIComponent(id)}`, { method: "DELETE" });
    reminders = reminders.filter((item) => item.id !== id);
    renderReminders();
  } catch (error) {
    button.disabled = false;
    setReminderStatus(remindersStatus, error.message || "Could not delete reminder.", true);
  }
}

function addChatMessage(role, text, sources = []) {
  const message = document.createElement("article");
  message.className = `chat-message ${role === "user" ? "chat-message-user" : "chat-message-assistant"}`;
  const speaker = document.createElement("div");
  speaker.className = "chat-speaker";
  speaker.textContent = role === "user" ? "YOU" : "TODAY MAIL AI";
  const content = document.createElement("p");
  content.textContent = text;
  message.append(speaker, content);

  if (sources.length) {
    const sourceSection = document.createElement("div");
    sourceSection.className = "chat-sources";
    const title = document.createElement("div");
    title.className = "chat-sources-title";
    title.textContent = "FROM YOUR EMAIL";
    sourceSection.append(title);
    for (const source of sources) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chat-source-card";
      const subject = document.createElement("span");
      subject.className = "chat-source-subject";
      subject.textContent = source.subject || "(No subject)";
      const details = document.createElement("span");
      details.className = "chat-source-details";
      details.textContent = [source.from || "Unknown sender", formatFullReceived(source.received)]
        .filter(Boolean)
        .join(" · ");
      const openLabel = document.createElement("span");
      openLabel.className = "chat-source-open";
      openLabel.textContent = "Open email ↗";
      button.append(subject, details, openLabel);
      button.addEventListener("click", () => void openEmail(source));
      sourceSection.append(button);
    }
    message.append(sourceSection);
  }

  askAiThread.append(message);
  message.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return message;
}

function resetAskAiChat() {
  askAiThread.querySelectorAll(".chat-message").forEach((message) => message.remove());
  document.querySelector("#ask-ai-empty").hidden = false;
  askAiInput.value = "";
  askAiHistory = [];
  askAiSend.disabled = false;
  askAiSend.textContent = "↑";
}

async function submitAskAiQuestion() {
  const question = askAiInput.value.trim();
  if (!question || askAiSend.disabled) return;
  const requestUserId = activeUserId;
  document.querySelector("#ask-ai-empty").hidden = true;
  addChatMessage("user", question);
  askAiInput.value = "";
  askAiInput.style.height = "";
  askAiSend.disabled = true;
  const originalSendLabel = askAiSend.textContent;
  askAiSend.textContent = "…";
  const pending = addChatMessage("assistant", "Searching your recent email…");

  try {
    const response = await apiFetch("/api/dashboard/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        connection_id: activeAccountFilter === "all" ? null : activeAccountFilter,
        history: askAiHistory.slice(-6),
      }),
    });
    const raw = await response.text();
    let payload;
    try {
      payload = raw ? JSON.parse(raw) : {};
    } catch {
      throw new Error(`The server returned an unreadable response (HTTP ${response.status}).`);
    }
    if (!response.ok) throw new Error(payload.detail || "Could not answer from your recent email.");
    pending.remove();
    if (activeUserId !== requestUserId) return;
    const answer = payload.answer || "I couldn't find an answer in those emails.";
    addChatMessage("assistant", answer, payload.sources || []);
    askAiHistory.push({ role: "user", content: question }, { role: "assistant", content: answer });
  } catch (error) {
    pending.remove();
    if (activeUserId === requestUserId) {
      addChatMessage("assistant", error.message || "Could not answer from your recent email.");
    }
  } finally {
    if (activeUserId === requestUserId) {
      askAiSend.disabled = false;
      askAiSend.textContent = originalSendLabel;
      askAiInput.focus();
    }
  }
}

function renderAskAiAccountScope() {
  const options = [
    { id: "all", email: "All connected accounts" },
    ...gmailAccounts.map((account) => ({ id: account.id, email: account.email })),
  ];
  chatAccountScope.replaceChildren(...options.map((account) => {
    const option = document.createElement("option");
    option.value = account.id;
    option.textContent = account.email;
    return option;
  }));
  if (
    gmailStatusChecked
    && activeAccountFilter !== "all"
    && !gmailAccounts.some((account) => account.id === activeAccountFilter)
  ) {
    activeAccountFilter = "all";
    resetAskAiChat();
  }
  chatAccountScope.value = activeAccountFilter;
}

function renderAccountFilters() {
  renderAskAiAccountScope();
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
      if (activeAccountFilter !== account.id) resetAskAiChat();
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
        showPage("inbox");
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
  card.title = "Open email";
  card.addEventListener("click", (event) => {
    if (event.target.closest("button, a, input, select, textarea")) return;
    void openEmail(email);
  });
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
  document.querySelector("#compose-open").disabled = gmailAccounts.length === 0;
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
  replyOpenButton.disabled = true;
  // A reminder only needs the saved email ID; it should not wait for body loading.
  remindEmailButton.disabled = !email?.id;
  composeDialog.close();
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
    replyOpenButton.disabled = false;
  } catch (error) {
    body.textContent = error.message || "Could not open this email.";
  }
}

function setComposeStatus(message, isError = false) {
  composeStatus.textContent = message;
  composeStatus.classList.toggle("error", isError);
  composeStatus.hidden = !message;
}

function selectedComposeConnectionId() {
  return composeMode === "reply"
    ? composeReplyEmail?.connection_id
    : document.querySelector("#compose-account").value;
}

function openComposer(mode, email = null) {
  composeMode = mode;
  composeReplyEmail = mode === "reply" ? email : null;
  const isReply = mode === "reply";
  const accountSelect = document.querySelector("#compose-account");
  accountSelect.replaceChildren(...gmailAccounts.map((account) => {
    const option = document.createElement("option");
    option.value = account.id;
    option.textContent = account.email;
    return option;
  }));
  if (isReply) accountSelect.value = email.connection_id || "";
  document.querySelector("#compose-title").textContent = isReply ? "Reply to email" : "Write an email";
  document.querySelector("#compose-kicker").textContent = isReply ? "REPLY" : "NEW MESSAGE";
  document.querySelector("#compose-account-row").hidden = isReply;
  document.querySelector("#compose-to").readOnly = isReply;
  document.querySelector("#compose-to").value = isReply ? (email.from || "") : "";
  document.querySelector("#compose-subject").value = isReply
    ? `Re: ${(email.subject || "").replace(/^Re:\\s*/i, "")}`
    : "";
  document.querySelector("#compose-instruction").value = "";
  document.querySelector("#compose-body").value = "";
  composeDraftArea.hidden = false;
  composeScheduleControls.hidden = true;
  composeStatus.hidden = true;
  composeGenerateButton.disabled = false;
  composeSendButton.disabled = false;
  document.querySelector("#compose-authorize").hidden = false;
  document.querySelector("#compose-send-at").value = "";
  if (!gmailAccounts.length) {
    setComposeStatus("Connect a Gmail account before writing a message.", true);
  }
  composeDialog.showModal();
  document.querySelector("#compose-to").focus();
}

async function generateComposeDraft() {
  const recipient = document.querySelector("#compose-to").value.trim();
  const subject = document.querySelector("#compose-subject").value.trim();
  const instruction = document.querySelector("#compose-instruction").value.trim();
  const connectionId = selectedComposeConnectionId();
  if (!connectionId) return setComposeStatus("Choose a connected Gmail account first.", true);
  if (!recipient) return setComposeStatus("Enter the recipient’s email address.", true);
  if (!subject) return setComposeStatus("Add a subject before drafting.", true);
  if (!instruction) return setComposeStatus("Tell the AI what you want the email to say.", true);
  composeGenerateButton.disabled = true;
  composeGenerateButton.textContent = "Writing…";
  setComposeStatus("The AI is preparing a draft. It will not send it.");
  try {
    const path = composeMode === "reply"
      ? `/api/dashboard/emails/${encodeURIComponent(composeReplyEmail.id)}/reply/draft`
      : "/api/dashboard/compose/draft";
    const requestBody = composeMode === "reply"
      ? { instruction }
      : { connection_id: connectionId, recipient, subject, instruction };
    const response = await apiFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not prepare the draft.");
    if (payload.recipient) document.querySelector("#compose-to").value = payload.recipient;
    if (payload.subject) document.querySelector("#compose-subject").value = composeMode === "reply"
      ? `Re: ${payload.subject.replace(/^Re:\\s*/i, "")}`
      : payload.subject;
    document.querySelector("#compose-body").value = payload.draft || "";
    composeDraftArea.hidden = !payload.draft;
    setComposeStatus(payload.draft ? "Draft ready. Review and edit it before sending or scheduling." : "The AI returned an empty draft.", !payload.draft);
  } catch (error) {
    setComposeStatus(error.message || "Could not prepare the draft.", true);
  } finally {
    composeGenerateButton.disabled = false;
    composeGenerateButton.textContent = "Draft with AI";
  }
}

async function authorizeGmailSending() {
  const connectionId = selectedComposeConnectionId();
  if (!connectionId) return setComposeStatus("Choose a Gmail account first.", true);
  const button = document.querySelector("#compose-authorize");
  button.disabled = true;
  button.textContent = "Opening Google…";
  try {
    const response = await apiFetch(
      `/api/gmail/accounts/${encodeURIComponent(connectionId)}/authorize-send`,
      { method: "POST" },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not open Google permissions.");
    window.location.assign(payload.authorization_url);
  } catch (error) {
    setComposeStatus(error.message || "Could not open Google permissions.", true);
    button.disabled = false;
    button.textContent = "Enable Gmail sending";
  }
}

async function sendComposedMessage() {
  const recipient = document.querySelector("#compose-to").value.trim();
  const subject = document.querySelector("#compose-subject").value.trim();
  const body = document.querySelector("#compose-body").value.trim();
  const connectionId = selectedComposeConnectionId();
  if (!recipient || !subject || !body || !connectionId) {
    return setComposeStatus("Check the recipient, subject, and message before sending.", true);
  }
  if (!window.confirm(`Send this message to ${recipient}?`)) return;
  composeSendButton.disabled = true;
  composeSendButton.textContent = "Sending…";
  try {
    const path = composeMode === "reply"
      ? `/api/dashboard/emails/${encodeURIComponent(composeReplyEmail.id)}/reply/send`
      : "/api/dashboard/compose/send";
    const requestBody = composeMode === "reply"
      ? { body }
      : { connection_id: connectionId, recipient, subject, body };
    const response = await apiFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not send the message.");
    setComposeStatus(composeMode === "reply" ? "Reply sent." : "Email sent.");
    composeDraftArea.hidden = true;
  } catch (error) {
    setComposeStatus(error.message || "Could not send the message.", true);
  } finally {
    composeSendButton.disabled = false;
    composeSendButton.textContent = "Send";
  }
}

async function scheduleComposedMessage() {
  const localValue = document.querySelector("#compose-send-at").value;
  const sendDate = localValue ? new Date(localValue) : null;
  const body = document.querySelector("#compose-body").value.trim();
  const recipient = document.querySelector("#compose-to").value.trim();
  const subject = document.querySelector("#compose-subject").value.trim();
  const connectionId = selectedComposeConnectionId();
  if (!sendDate || Number.isNaN(sendDate.getTime()) || sendDate <= new Date()) {
    return setComposeStatus("Choose a future date and time.", true);
  }
  if (!body || !recipient || !subject || !connectionId) {
    return setComposeStatus("Check the recipient, subject, and message before scheduling.", true);
  }
  const button = document.querySelector("#compose-confirm-schedule");
  button.disabled = true;
  button.textContent = "Scheduling…";
  try {
    const path = composeMode === "reply"
      ? `/api/dashboard/emails/${encodeURIComponent(composeReplyEmail.id)}/reply/schedule`
      : "/api/dashboard/compose/schedule";
    const requestBody = composeMode === "reply"
      ? { body, send_at: sendDate.toISOString() }
      : { connection_id: connectionId, recipient, subject, body, send_at: sendDate.toISOString() };
    const response = await apiFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not schedule the message.");
    setComposeStatus(`Email scheduled. The timer checks for due messages every five minutes.`);
    composeScheduleControls.hidden = true;
    composeDraftArea.hidden = true;
  } catch (error) {
    setComposeStatus(error.message || "Could not schedule the message.", true);
  } finally {
    button.disabled = false;
    button.textContent = "Schedule email";
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
inboxNav.addEventListener("click", () => showPage("inbox"));
askAiNav.addEventListener("click", () => showPage("ask-ai"));
remindersNav.addEventListener("click", () => showPage("reminders"));
document.querySelectorAll(".reminder-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    activeReminderTab = tab.dataset.reminderTab;
    document.querySelectorAll(".reminder-tab").forEach((item) => item.classList.toggle("selected", item === tab));
    renderReminders();
  });
});
remindEmailButton.addEventListener("click", () => openReminderForm());
reminderForm.addEventListener("submit", (event) => void submitReminder(event));
document.querySelector("#close-reminder-dialog").addEventListener("click", () => reminderDialog.close());
document.querySelector("#cancel-reminder").addEventListener("click", () => reminderDialog.close());
reminderDialog.addEventListener("click", (event) => {
  if (event.target === reminderDialog) reminderDialog.close();
});
chatAccountScope.addEventListener("change", () => {
  if (activeAccountFilter !== chatAccountScope.value) resetAskAiChat();
  activeAccountFilter = chatAccountScope.value;
  rememberAccountFilter();
  renderDashboard();
});
document.querySelectorAll(".ask-ai-prompt").forEach((button) => {
  button.addEventListener("click", () => {
    const input = document.querySelector("#ask-ai-input");
    input.value = button.dataset.question || "";
    input.focus();
  });
});
askAiComposer.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitAskAiQuestion();
});
askAiInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void submitAskAiQuestion();
  }
});
document.querySelector("#empty-connect").addEventListener("click", connectGmail);
document.querySelector("#close-email-dialog").addEventListener("click", () => emailDialog.close());
replyOpenButton.addEventListener("click", () => {
  if (!selectedEmail) return;
  emailDialog.close();
  openComposer("reply", selectedEmail);
});
document.querySelector("#compose-open").addEventListener("click", () => openComposer("new"));
document.querySelector("#compose-close").addEventListener("click", () => composeDialog.close());
composeGenerateButton.addEventListener("click", () => void generateComposeDraft());
document.querySelector("#compose-authorize").addEventListener("click", () => void authorizeGmailSending());
composeSendButton.addEventListener("click", () => void sendComposedMessage());
document.querySelector("#compose-show-schedule").addEventListener("click", () => {
  const input = document.querySelector("#compose-send-at");
  const now = new Date();
  now.setMinutes(now.getMinutes() + 1);
  input.min = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  composeScheduleControls.hidden = false;
  input.focus();
});
document.querySelector("#compose-confirm-schedule").addEventListener("click", () => void scheduleComposedMessage());
composeDialog.addEventListener("click", (event) => {
  if (event.target === composeDialog) composeDialog.close();
});
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
