(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const elements = {
    loginView: $("login-view"),
    loginForm: $("login-form"),
    loginError: $("login-error"),
    password: $("password"),
    consoleView: $("console-view"),
    logoutButton: $("logout-button"),
    refreshButton: $("refresh-button"),
    streamState: $("stream-state"),
    streamStateLabel: $("stream-state-label"),
    observedAt: $("observed-at"),
    degradedBanner: $("degraded-banner"),
    degradedDetail: $("degraded-detail"),
    hostJournal: $("host-journal"),
    hostJournalList: $("host-journal-list"),
    nodeState: $("node-state"),
    nodeDetail: $("node-detail"),
    activityState: $("activity-state"),
    sessionDetail: $("session-detail"),
    queuedQuestions: $("queued-questions"),
    pendingMessages: $("pending-messages"),
    commandCount: $("command-count"),
    activeSession: $("active-session"),
    sessionTitle: $("session-title"),
    sessionQuestion: $("session-question"),
    sessionState: $("session-state"),
    sessionTurns: $("session-turns"),
    sessionActions: $("session-actions"),
    sessionTokens: $("session-tokens"),
    timelineList: $("timeline-list"),
    timelineEmpty: $("timeline-empty"),
    timelineCount: $("timeline-count"),
    messageForm: $("message-form"),
    messageBody: $("message-body"),
    messagePriority: $("message-priority"),
    messageLength: $("message-length"),
    messageList: $("message-list"),
    messageEmpty: $("message-empty"),
    toastRegion: $("toast-region"),
  };

  const state = {
    csrfToken: null,
    activeSessionId: null,
    eventSource: null,
    refreshTimer: null,
    authCheckTimer: null,
    refreshing: false,
    timeline: new Map(),
    pendingMessage: null,
    pendingCommands: new Map(),
    commandApiEnabled: false,
  };

  const labels = {
    node: {
      sleeping: "Ожидает",
      paused: "На паузе",
    },
    activity: {
      idle: "Свободен",
      running: "Исследует",
    },
    hostTransition: {
      checking: "проверка",
      retry_wait: "ожидание повтора",
      ready_to_start: "готов к запуску",
      resume_degraded: "медленное восстановление",
      resume_blocked: "запуск заблокирован",
      resolved: "завершён",
    },
    message: {
      created: "создано",
      queued: "в очереди",
      delivered: "доставлено",
      acknowledged: "принято",
      answered: "отвечено",
      expired: "истекло",
    },
    session: {
      created: "создана",
      waking: "пробуждение",
      orienting: "ориентация",
      selecting_question: "выбор вопроса",
      planning: "планирование",
      exploring: "исследование",
      verifying: "проверка",
      stopping: "остановка",
      consolidating: "консолидация",
      reporting: "отчёт",
      committing: "фиксация",
      reconciling_commit: "сверка коммита",
      aborting: "прерывание",
      succeeded: "завершена",
      succeeded_partial: "частично завершена",
      failed: "ошибка",
      cancelled: "отменена",
    },
    degraded: {
      unit_state_missing: "нет снимка systemd",
      unit_state_invalid: "снимок systemd некорректен",
      unit_state_stale: "снимок systemd устарел",
      boot_id_mismatch: "снимок относится к прошлой загрузке",
      unit_state_publisher_failed: "publisher состояния завершился ошибкой",
      runtime_inactive: "контур выполнения остановлен",
      runtime_member_unhealthy: "один из процессов контура нездоров",
      maintenance_active: "идёт offline-обслуживание",
      host_transition_in_progress: "переход состояния хоста не завершён",
      host_transition_invalid: "журнал перехода хоста некорректен",
      host_policy_change_in_progress: "смена host policy не завершена",
      database_unavailable: "операционная база недоступна",
    },
  };

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (options.mutation) {
      if (!state.csrfToken) {
        throw new ApiError(401, "Сессия владельца недоступна");
      }
      headers.set("X-CSRF-Token", state.csrfToken);
    }
    const response = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (response.status === 401) {
      leaveConsole();
      throw new ApiError(401, "Сессия истекла — войдите снова");
    }
    if (!response.ok) {
      let message = `Ошибка запроса (${response.status})`;
      try {
        const problem = await response.json();
        if (typeof problem.detail === "string") {
          message = problem.detail;
        } else if (problem.detail && problem.detail.code === "runtime_unavailable") {
          message = "Контур выполнения сейчас доступен только для чтения";
        }
      } catch (_error) {
        // The public message above is deliberately generic for non-JSON failures.
      }
      throw new ApiError(response.status, message);
    }
    return response.status === 204 ? null : response.json();
  }

  async function bootstrap() {
    try {
      const response = await fetch("/api/auth/session", {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        showLogin();
        return;
      }
      enterConsole(await response.json());
    } catch (_error) {
      showLogin("Локальный API сейчас недоступен");
    }
  }

  function showLogin(errorMessage = "") {
    elements.consoleView.hidden = true;
    elements.loginView.hidden = false;
    elements.logoutButton.hidden = true;
    elements.refreshButton.hidden = true;
    elements.streamState.hidden = true;
    elements.loginError.hidden = !errorMessage;
    elements.loginError.textContent = errorMessage;
    window.setTimeout(() => elements.password.focus(), 0);
  }

  function enterConsole(session) {
    state.csrfToken = session.csrf_token;
    state.commandApiEnabled = false;
    setMutationAvailability(false);
    elements.loginView.hidden = true;
    elements.consoleView.hidden = false;
    elements.logoutButton.hidden = false;
    elements.refreshButton.hidden = false;
    elements.streamState.hidden = false;
    setConnection("connecting", "Подключение…");
    void refreshData();
    connectTimeline();
    clearInterval(state.refreshTimer);
    state.refreshTimer = window.setInterval(() => void refreshData(), 15000);
  }

  function leaveConsole(message = "") {
    state.csrfToken = null;
    state.activeSessionId = null;
    state.timeline.clear();
    state.pendingMessage = null;
    state.pendingCommands.clear();
    clearInterval(state.refreshTimer);
    clearTimeout(state.authCheckTimer);
    state.refreshTimer = null;
    closeTimeline();
    showLogin(message);
  }

  async function login(event) {
    event.preventDefault();
    const submit = elements.loginForm.querySelector("button[type='submit']");
    setBusy(submit, true);
    elements.loginError.hidden = true;
    try {
      const session = await api("/api/auth/login", {
        method: "POST",
        body: { password: elements.password.value },
      });
      elements.password.value = "";
      enterConsole(session);
    } catch (error) {
      elements.password.value = "";
      elements.loginError.textContent = humanError(error, "Не удалось войти");
      elements.loginError.hidden = false;
      elements.password.focus();
    } finally {
      setBusy(submit, false);
    }
  }

  async function logout() {
    elements.logoutButton.disabled = true;
    try {
      await api("/api/auth/logout", { method: "POST", mutation: true });
      leaveConsole();
    } catch (error) {
      if (error.status !== 401) {
        toast(humanError(error, "Не удалось завершить сессию"), true);
      }
    } finally {
      elements.logoutButton.disabled = false;
    }
  }

  async function refreshData(announce = false) {
    if (state.refreshing || !state.csrfToken) {
      return;
    }
    state.refreshing = true;
    elements.refreshButton.disabled = true;
    elements.refreshButton.setAttribute("aria-busy", "true");
    const requests = await Promise.allSettled([
      api("/api/status"),
      api("/api/timeline?limit=50"),
      api("/api/messages?limit=8"),
    ]);
    if (requests[0].status === "fulfilled") {
      renderStatus(requests[0].value);
    }
    if (requests[1].status === "fulfilled") {
      mergeTimeline(requests[1].value.items);
    }
    if (requests[2].status === "fulfilled") {
      renderMessages(requests[2].value.items);
    }
    const failures = requests.filter((result) => result.status === "rejected");
    if (failures.length > 0 && state.csrfToken) {
      setConnection("offline", "Данные недоступны");
      if (announce) {
        toast(humanError(failures[0].reason, "Не удалось обновить данные"), true);
      }
    } else if (announce) {
      toast("Данные обновлены");
    }
    state.refreshing = false;
    elements.refreshButton.disabled = false;
    elements.refreshButton.removeAttribute("aria-busy");
  }

  function renderStatus(status) {
    const operational = status.operational;
    const active = operational ? operational.active_session : null;
    const scheduler = operational ? operational.scheduler : null;
    state.commandApiEnabled = status.command_api_enabled;
    state.activeSessionId = active ? active.id : null;
    elements.degradedBanner.hidden = status.mode !== "degraded";
    elements.degradedDetail.textContent = status.reasons.length
      ? `Причины: ${status.reasons
          .map((reason) => labels.degraded[reason] || reason)
          .join(", ")}. Команды отключены.`
      : "Сайт работает только для чтения.";
    if (status.host.host_transition) {
      const transition = status.host.host_transition;
      const retry = transition.next_attempt_at
        ? `, повтор ${formatDate(transition.next_attempt_at, true)}`
        : "";
      elements.degradedDetail.textContent +=
        ` Переход: ${labels.hostTransition[transition.state] || transition.state}, ` +
        `попытка ${transition.current_attempt_seq}${retry}.`;
    }
    renderHostJournal(status.host.host_transition_events || []);
    if (!operational) {
      elements.nodeState.textContent = "Недоступен";
      elements.nodeDetail.textContent = "Operational store не отвечает";
      elements.activityState.textContent = "Нет данных";
      elements.sessionDetail.textContent = "Активная сессия неизвестна";
      elements.queuedQuestions.textContent = "—";
      elements.pendingMessages.textContent = "—";
      elements.commandCount.textContent = "Команды отключены";
      elements.observedAt.textContent = `Срез ${formatDate(status.observed_at, true)}`;
      renderSession(null);
      setMutationAvailability(false);
      return;
    }
    elements.nodeState.textContent =
      labels.node[operational.node_state] || operational.node_state;
    elements.nodeDetail.textContent =
      operational.node_state === "paused"
        ? "Новые сессии заблокированы"
        : active
          ? "Познавательный цикл выполняется"
          : scheduler && scheduler.busy
            ? "Планировщик выполняет пробуждение"
            : scheduler &&
                scheduler.backoff_until &&
                Date.parse(scheduler.backoff_until) > Date.now()
              ? `Повтор после ${formatDate(scheduler.backoff_until, true)}`
              : scheduler && scheduler.next_scheduled_at
                ? `Следующее пробуждение ${formatDate(scheduler.next_scheduled_at, true)}`
                : "Готов к следующему циклу";
    elements.activityState.textContent =
      labels.activity[operational.activity] || operational.activity;
    elements.sessionDetail.textContent = active
      ? `Сессия ${shortId(active.id)}`
      : "Активной сессии нет";
    elements.queuedQuestions.textContent = formatNumber(operational.queued_questions);
    elements.pendingMessages.textContent = formatNumber(operational.pending_messages);
    elements.commandCount.textContent = `${formatNumber(operational.pending_commands)} ${plural(
      operational.pending_commands,
      "команда ожидает",
      "команды ожидают",
      "команд ожидают",
    )}`;
    elements.observedAt.textContent = `Срез ${formatDate(status.observed_at, true)}`;
    renderSession(active);
    setMutationAvailability(status.command_api_enabled);
  }

  function renderHostJournal(events) {
    elements.hostJournal.hidden = events.length === 0;
    elements.hostJournalList.replaceChildren(
      ...events.map((event) => {
        const item = document.createElement("li");
        const stateLabel = labels.hostTransition[event.to_state] || event.to_state;
        item.textContent = `${formatDate(event.occurred_at, true)} · ${stateLabel} · ${event.reason}`;
        return item;
      }),
    );
  }

  function setMutationAvailability(enabled) {
    const messageButton = elements.messageForm.querySelector("button[type='submit']");
    messageButton.disabled = !enabled;
    document.querySelectorAll(".control-button").forEach((button) => {
      button.disabled = !enabled || (button.classList.contains("session-command") && !state.activeSessionId);
    });
  }

  function renderSession(session) {
    elements.activeSession.hidden = !session;
    if (!session) {
      return;
    }
    elements.sessionTitle.textContent = `Сессия ${shortId(session.id)}`;
    elements.sessionQuestion.textContent = session.question_text || "Системная сессия без вопроса";
    elements.sessionState.textContent = labels.session[session.state] || session.state;
    elements.sessionTurns.textContent = formatNumber(session.usage.model_turns);
    elements.sessionActions.textContent = formatNumber(session.usage.tool_actions);
    elements.sessionTokens.textContent = formatNumber(
      session.usage.input_tokens + session.usage.output_tokens,
    );
  }

  function mergeTimeline(events) {
    events.forEach((event) => state.timeline.set(event.id, event));
    const ordered = Array.from(state.timeline.values())
      .sort((left, right) => {
        const timeDifference = Date.parse(right.occurred_at) - Date.parse(left.occurred_at);
        return timeDifference || right.id.localeCompare(left.id);
      })
      .slice(0, 200);
    state.timeline = new Map(ordered.map((event) => [event.id, event]));
    elements.timelineList.replaceChildren(...ordered.map(timelineItem));
    elements.timelineList.hidden = ordered.length === 0;
    elements.timelineEmpty.hidden = ordered.length !== 0;
    elements.timelineCount.textContent = `${ordered.length} ${plural(
      ordered.length,
      "событие",
      "события",
      "событий",
    )}`;
  }

  function timelineItem(event) {
    const item = document.createElement("li");
    item.className = "timeline-item";

    const rail = document.createElement("span");
    rail.className = "timeline-rail";
    const node = document.createElement("span");
    node.className = "timeline-node";
    node.setAttribute("aria-hidden", "true");
    rail.append(node);

    const body = document.createElement("div");
    body.className = "timeline-body";
    const meta = document.createElement("div");
    meta.className = "timeline-meta";
    const type = document.createElement("span");
    type.className = "event-type";
    type.textContent = splitEventType(event.type);
    const actor = document.createElement("span");
    actor.className = "event-actor";
    actor.textContent = `· ${event.actor}`;
    meta.append(type, actor);
    const summary = document.createElement("p");
    summary.className = "timeline-summary";
    summary.textContent = event.summary;
    body.append(meta, summary);

    const time = document.createElement("time");
    time.className = "timeline-time";
    time.dateTime = event.occurred_at;
    time.textContent = formatDate(event.occurred_at);
    item.append(rail, body, time);
    return item;
  }

  function renderMessages(messages) {
    elements.messageList.replaceChildren(...messages.map(messageItem));
    elements.messageEmpty.hidden = messages.length !== 0;
  }

  function messageItem(message) {
    const item = document.createElement("li");
    item.className = "message-item";
    const body = document.createElement("p");
    body.textContent = message.body;
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const stateBadge = document.createElement("span");
    stateBadge.className = "state-badge";
    stateBadge.textContent = labels.message[message.state] || message.state;
    const time = document.createElement("time");
    time.dateTime = message.created_at;
    time.textContent = formatDate(message.created_at);
    meta.append(stateBadge, time);
    item.append(body, meta);
    return item;
  }

  function connectTimeline() {
    closeTimeline();
    if (!state.csrfToken) {
      return;
    }
    setConnection("connecting", "Подключение…");
    const source = new EventSource("/api/timeline/stream?after=0", { withCredentials: true });
    state.eventSource = source;
    source.addEventListener("open", () => setConnection("online", "Хронология онлайн"));
    source.addEventListener("timeline", (event) => {
      try {
        mergeTimeline([JSON.parse(event.data)]);
        setConnection("online", "Хронология онлайн");
      } catch (_error) {
        setConnection("offline", "Некорректное событие");
      }
    });
    source.addEventListener("error", (event) => {
      setConnection("connecting", "Переподключение…");
      if (event instanceof MessageEvent && event.data) {
        toast("Поток хронологии временно недоступен", true);
      }
      scheduleSessionCheck();
    });
  }

  function closeTimeline() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  function scheduleSessionCheck() {
    clearTimeout(state.authCheckTimer);
    state.authCheckTimer = window.setTimeout(async () => {
      try {
        const response = await fetch("/api/auth/session", {
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (response.status === 401) {
          leaveConsole("Сессия истекла — войдите снова");
        }
      } catch (_error) {
        setConnection("offline", "API недоступен");
      }
    }, 2200);
  }

  function setConnection(kind, label) {
    elements.streamState.className = `connection-pill is-${kind}`;
    elements.streamStateLabel.textContent = label;
  }

  async function submitMessage(event) {
    event.preventDefault();
    const body = elements.messageBody.value.trim();
    if (!body) {
      return;
    }
    const priority = Number(elements.messagePriority.value);
    const signature = JSON.stringify({ body, priority });
    if (!state.pendingMessage || state.pendingMessage.signature !== signature) {
      state.pendingMessage = {
        signature,
        idempotencyKey: crypto.randomUUID(),
      };
    }
    const submit = elements.messageForm.querySelector("button[type='submit']");
    setBusy(submit, true);
    try {
      await api("/api/messages", {
        method: "POST",
        mutation: true,
        body: {
          idempotency_key: state.pendingMessage.idempotencyKey,
          body,
          priority,
          expires_in_seconds: 604800,
        },
      });
      state.pendingMessage = null;
      elements.messageBody.value = "";
      updateMessageLength();
      toast("Сообщение поставлено в durable очередь");
      await refreshData();
    } catch (error) {
      if (error.status === 409) {
        state.pendingMessage = null;
      }
      if (error.status === 503) {
        state.commandApiEnabled = false;
        void refreshData();
      }
      if (error.status !== 401) {
        toast(humanError(error, "Не удалось отправить сообщение"), true);
      }
    } finally {
      setBusy(submit, false);
      submit.disabled = !state.commandApiEnabled;
    }
  }

  async function submitCommand(button) {
    const type = button.dataset.command;
    if (!type) {
      return;
    }
    const sessionScoped = type === "stop_gracefully" || type === "abort_session";
    const sessionId = sessionScoped ? state.activeSessionId : null;
    if (sessionScoped && !sessionId) {
      toast("Активной сессии нет", true);
      return;
    }
    if (
      type === "abort_session" &&
      !window.confirm("Прервать активную сессию? Незавершённый staging будет отменён.")
    ) {
      return;
    }
    const signature = `${type}:${sessionId || "global"}`;
    if (!state.pendingCommands.has(signature)) {
      state.pendingCommands.set(signature, crypto.randomUUID());
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      await api("/api/operator-commands", {
        method: "POST",
        mutation: true,
        body: {
          idempotency_key: state.pendingCommands.get(signature),
          type,
          session_id: sessionId,
          arguments: {},
          reason: "requested from local owner console",
        },
      });
      state.pendingCommands.delete(signature);
      toast(commandSuccess(type));
      await refreshData();
    } catch (error) {
      if (error.status === 409) {
        state.pendingCommands.delete(signature);
      }
      if (error.status === 503) {
        state.commandApiEnabled = false;
        void refreshData();
      }
      if (error.status !== 401) {
        toast(humanError(error, "Команда не принята"), true);
      }
    } finally {
      button.removeAttribute("aria-busy");
      button.disabled =
        !state.commandApiEnabled || (sessionScoped && !state.activeSessionId);
    }
  }

  function commandSuccess(type) {
    const messages = {
      wake_now: "Пробуждение поставлено в очередь",
      pause: "Пауза поставлена в очередь",
      resume: "Возобновление поставлено в очередь",
      stop_gracefully: "Безопасная остановка запрошена",
      abort_session: "Прерывание сессии запрошено",
    };
    return messages[type] || "Команда принята";
  }

  function setBusy(button, busy) {
    button.disabled = busy;
    button.classList.toggle("is-busy", busy);
    if (busy) {
      button.setAttribute("aria-busy", "true");
    } else {
      button.removeAttribute("aria-busy");
    }
  }

  function updateMessageLength() {
    elements.messageLength.textContent = `${elements.messageBody.value.length} / 4096`;
  }

  function toast(message, isError = false) {
    const notification = document.createElement("div");
    notification.className = `toast${isError ? " is-error" : ""}`;
    notification.textContent = message;
    elements.toastRegion.append(notification);
    window.setTimeout(() => notification.remove(), 4800);
  }

  function humanError(error, fallback) {
    if (error instanceof ApiError && error.message) {
      const translations = {
        "authentication required": "Требуется вход владельца",
        "invalid credentials": "Неверный пароль",
        "request origin is not allowed": "Источник запроса не разрешён",
        "rate limit exceeded": "Слишком много запросов — попробуйте позже",
        "operational state is unavailable": "Операционное состояние недоступно",
        "inbox request conflicts with durable state": "Запрос конфликтует с durable состоянием",
      };
      return translations[error.message] || error.message;
    }
    return fallback;
  }

  function splitEventType(value) {
    return value.replace(/([a-z0-9])([A-Z])/g, "$1 $2").toUpperCase();
  }

  function shortId(value) {
    return value.slice(0, 8);
  }

  function formatDate(value, withDate = false) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "неизвестное время";
    }
    return new Intl.DateTimeFormat("ru-RU", {
      day: withDate ? "2-digit" : undefined,
      month: withDate ? "short" : undefined,
      hour: "2-digit",
      minute: "2-digit",
      second: withDate ? "2-digit" : undefined,
    }).format(date);
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("ru-RU").format(value);
  }

  function plural(value, one, few, many) {
    const absolute = Math.abs(value) % 100;
    const last = absolute % 10;
    if (absolute > 10 && absolute < 20) {
      return many;
    }
    if (last === 1) {
      return one;
    }
    if (last >= 2 && last <= 4) {
      return few;
    }
    return many;
  }

  elements.loginForm.addEventListener("submit", login);
  elements.logoutButton.addEventListener("click", () => void logout());
  elements.refreshButton.addEventListener("click", () => void refreshData(true));
  elements.messageForm.addEventListener("submit", submitMessage);
  elements.messageBody.addEventListener("input", updateMessageLength);
  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => void submitCommand(button));
  });
  window.addEventListener("beforeunload", closeTimeline);

  void bootstrap();
})();
