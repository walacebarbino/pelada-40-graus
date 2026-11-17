const matchesContainer = document.querySelector("#matches");
const matchesPanel = document.querySelector("#matches-panel");
const reservationsPanel = document.querySelector("#reservations-panel");
const adminPanel = document.querySelector("#admin-panel");
const matchesFeedback = document.querySelector("#matches-feedback");
const adminMatchesContainer = document.querySelector("#admin-matches");
const navMatchesButton = document.querySelector("#nav-matches");
const navReservationsButton = document.querySelector("#nav-reservations");
const navAdminButton = document.querySelector("#nav-admin");
const savedReservationsContainer = document.querySelector("#saved-reservations");
const reservationsFeedback = document.querySelector("#reservations-feedback");
const numberFormatter = new Intl.NumberFormat("pt-BR");
const currencyFormatter = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});
const bookingDialog = document.querySelector("#booking-dialog");
const bookingTitle = document.querySelector("#booking-title");
const closeDialogButton = document.querySelector("#close-dialog");
const bookingForm = document.querySelector("#booking-form");
const playerNameInput = document.querySelector("#player-name");
const playerPhoneInput = document.querySelector("#player-phone");
const playerNameError = document.querySelector("#player-name-error");
const playerPhoneError = document.querySelector("#player-phone-error");
const formFeedback = document.querySelector("#form-feedback");
const selectedSchedule = document.querySelector("#selected-schedule");
const selectedPrice = document.querySelector("#selected-price");
const selectedAvailability = document.querySelector("#selected-availability");
const reservationFeatureStatus = document.querySelector("#reservation-feature-status");
const createReservationButton = document.querySelector("#create-reservation");
const reservationsHeading = document.querySelector("#reservations-heading");
const participantsDialog = document.querySelector("#participants-dialog");
const participantsTitle = document.querySelector("#participants-title");
const participantsSchedule = document.querySelector("#participants-schedule");
const participantsList = document.querySelector("#participants-list");
const closeParticipantsButton = document.querySelector("#close-participants");
const deleteMatchDialog = document.querySelector("#delete-match-dialog");
const deleteMatchTitle = document.querySelector("#delete-match-title");
const deleteMatchSummaryTitle = document.querySelector("#delete-match-summary-title");
const deleteMatchSummaryDate = document.querySelector("#delete-match-summary-date");
const deleteMatchSummaryTime = document.querySelector("#delete-match-summary-time");
const deleteMatchSummaryId = document.querySelector("#delete-match-summary-id");
const deleteMatchFeedback = document.querySelector("#delete-match-feedback");
const confirmDeleteMatchButton = document.querySelector("#confirm-delete-match");
const cancelDeleteMatchButton = document.querySelector("#cancel-delete-match");
const refreshAfterDeleteButton = document.querySelector("#refresh-after-delete");
const adminForm = document.querySelector("#admin-form");
const adminFormHeading = document.querySelector("#admin-form-heading");
const adminFieldset = document.querySelector("#admin-fieldset");
const adminKeyInput = document.querySelector("#admin-key");
const adminFeedback = document.querySelector("#admin-feedback");
const createMatchButton = document.querySelector("#create-match");
const cancelEditButton = document.querySelector("#cancel-edit");
const RESERVATION_STORAGE_KEY = "pelada.reservations.v1";
const RESERVATION_STORAGE_VERSION = 1;
const CREATE_RESERVATION_PATH = "/api/v2/reservas";
const RECOVER_RESERVATION_PATH = "/api/v2/reservas/recuperacao";
const RESERVATION_CAPABILITIES_PATH = "/api/ui-capabilities";
const RESERVATION_TIMEOUT_MS = 10_000;

let returnFocusTo = null;
let returnFocusToParticipants = null;
let validationAttempted = false;
let adminKeyInMemory = "";
let adminValidationAttempted = false;
let editingMatchId = null;
let currentAdminMatches = new Map();
let participantsRequestId = 0;
let selectedParticipantsMatchId = null;
let selectedBookingMatchId = null;
let pendingDeleteMatchId = null;
let returnFocusToDelete = null;
let deletePending = false;
let deleteNeedsRefresh = false;
let currentMatches = new Map();
let reservationHttpEnabled = false;
let reservationCryptoAvailable = false;
let reservationStorageAvailable = false;
let reservationRecordsReadable = true;
let reservationRequestActive = false;
let reservationRecords = [];
let reservationCountdownTimer = null;
let bookingSucceeded = false;

function showMessage(title, message, canRetry) {
  const card = document.createElement("div");
  card.className = "state-card";

  const heading = document.createElement("p");
  heading.className = "state-title";
  heading.textContent = title;

  const description = document.createElement("p");
  description.textContent = message;
  card.append(heading, description);

  if (canRetry) {
    const retryButton = document.createElement("button");
    retryButton.className = "retry-button";
    retryButton.type = "button";
    retryButton.textContent = "Tentar novamente";
    retryButton.addEventListener("click", loadMatches);
    card.append(retryButton);
  }

  matchesContainer.replaceChildren(card);
  matchesContainer.setAttribute("aria-busy", "false");
}

function showAdminMessage(title, message, canRetry) {
  currentAdminMatches = new Map();
  const card = document.createElement("div");
  card.className = "state-card";

  const heading = document.createElement("p");
  heading.className = "state-title";
  heading.textContent = title;

  const description = document.createElement("p");
  description.textContent = message;
  card.append(heading, description);

  if (canRetry) {
    const retryButton = document.createElement("button");
    retryButton.className = "retry-button";
    retryButton.type = "button";
    retryButton.textContent = "Tentar novamente";
    retryButton.addEventListener("click", loadAdminMatches);
    card.append(retryButton);
  }

  adminMatchesContainer.replaceChildren(card);
  adminMatchesContainer.setAttribute("aria-busy", "false");
}

function showLoading() {
  const skeletons = document.createDocumentFragment();
  for (let index = 0; index < 3; index += 1) {
    const skeleton = document.createElement("div");
    skeleton.className = "skeleton";
    skeleton.setAttribute("aria-hidden", "true");
    skeletons.append(skeleton);
  }

  matchesContainer.replaceChildren(skeletons);
  matchesContainer.setAttribute("aria-busy", "true");
}

function isValidMatch(match) {
  return (
    match !== null &&
    typeof match === "object" &&
    Number.isInteger(match.id) &&
    typeof match.title === "string" &&
    typeof match.match_date === "string" &&
    typeof match.match_time === "string" &&
    typeof match.location === "string" &&
    Number.isInteger(match.duration_minutes) &&
    Number.isInteger(match.max_players) &&
    Number.isSafeInteger(match.price_per_slot_cents) &&
    match.price_per_slot_cents >= 0 &&
    typeof match.status === "string" &&
    Number.isInteger(match.vagas_ocupadas) &&
    Number.isInteger(match.vagas_livres) &&
    match.max_players >= 0 &&
    match.vagas_ocupadas >= 0 &&
    match.vagas_livres >= 0 &&
    match.vagas_livres <= match.max_players
  );
}

function createMatchCard(match) {
  const card = document.createElement("article");
  card.className = "match-card";

  const top = document.createElement("div");
  top.className = "match-card-top";

  const schedule = document.createElement("div");
  const label = document.createElement("p");
  label.className = "match-label";
  label.textContent = match.title;
  const time = document.createElement("h3");
  time.className = "match-time";
  time.textContent = match.match_time;
  schedule.append(label, time);

  const id = document.createElement("span");
  id.className = "match-id";
  id.textContent = `#${numberFormatter.format(match.id)}`;
  top.append(schedule, id);

  const availability = document.createElement("div");
  availability.className = "availability";

  const availabilityRow = document.createElement("div");
  availabilityRow.className = "availability-row";
  const availabilityLabel = document.createElement("span");
  availabilityLabel.className = "availability-label";
  availabilityLabel.textContent = "Vagas disponíveis";
  const availabilityCount = document.createElement("strong");
  availabilityCount.className = "availability-count";
  availabilityCount.textContent =
    `${numberFormatter.format(match.vagas_livres)} de ` +
    `${numberFormatter.format(match.max_players)} vagas`;
  availabilityRow.append(availabilityLabel, availabilityCount);

  const track = document.createElement("div");
  track.className = "availability-track";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", "Disponibilidade de vagas");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", String(match.max_players));
  track.setAttribute("aria-valuenow", String(match.vagas_livres));

  const fill = document.createElement("div");
  fill.className = "availability-fill";
  const percentage =
    match.max_players === 0 ? 0 : (match.vagas_livres / match.max_players) * 100;
  fill.style.width = `${percentage}%`;
  track.append(fill);
  availability.append(availabilityRow, track);

  const priceRow = document.createElement("div");
  priceRow.className = "price-row";
  const priceLabel = document.createElement("span");
  priceLabel.className = "price-label";
  priceLabel.textContent = "Preço";
  const price = document.createElement("strong");
  price.className = "price-value";
  price.textContent = currencyFormatter.format(match.price_per_slot_cents / 100);
  priceRow.append(priceLabel, price);

  const chooseButton = document.createElement("button");
  chooseButton.className = match.vagas_livres > 0 ? "choose-button" : "sold-out-button";
  chooseButton.type = "button";
  chooseButton.textContent = match.vagas_livres > 0 ? "Escolher horário" : "Esgotado";
  chooseButton.disabled = match.vagas_livres <= 0;
  chooseButton.setAttribute(
    "aria-label",
    match.vagas_livres > 0
      ? `Escolher horário ${match.match_time}`
      : `Partida das ${match.match_time} esgotada`,
  );
  if (match.vagas_livres > 0) {
    chooseButton.addEventListener("click", () => openBookingForm(match, chooseButton));
  }

  const participantsButton = document.createElement("button");
  participantsButton.className = "participants-button";
  participantsButton.type = "button";
  participantsButton.textContent = "Ver participantes";
  participantsButton.setAttribute(
    "aria-label",
    `Ver participantes confirmados da partida das ${match.match_time}`,
  );
  participantsButton.addEventListener("click", () =>
    openParticipants(match, participantsButton),
  );

  card.append(top, availability, priceRow, chooseButton, participantsButton);
  return card;
}

function createAdminMatchCard(match) {
  const card = document.createElement("article");
  card.className = "match-card admin-match-card";

  const top = document.createElement("div");
  top.className = "match-card-top";

  const title = document.createElement("h3");
  title.className = "match-time";
  title.textContent = match.title;

  const id = document.createElement("span");
  id.className = "match-id";
  id.textContent = `#${numberFormatter.format(match.id)}`;
  top.append(title, id);

  const details = document.createElement("div");
  details.className = "admin-match-details";

  const displayDate = formatMatchDate(match.match_date);

  for (const [label, value] of [
    ["Data", displayDate],
    ["Horário", match.match_time],
    ["Local", match.location],
    ["Status", match.status],
    ["Preço por vaga", currencyFormatter.format(match.price_per_slot_cents / 100)],
  ]) {
    const row = document.createElement("p");
    const rowLabel = document.createElement("span");
    rowLabel.textContent = label;
    const rowValue = document.createElement("strong");
    rowValue.textContent = value;
    row.append(rowLabel, rowValue);
    details.append(row);
  }

  const editButton = document.createElement("button");
  editButton.className = "edit-match-button";
  editButton.type = "button";
  editButton.textContent = "Editar";
  editButton.setAttribute(
    "aria-label",
    `Editar ${match.title}, ${displayDate}, às ${match.match_time}, ${match.location}`,
  );
  editButton.dataset.matchId = String(match.id);

  const deleteButton = document.createElement("button");
  deleteButton.className = "delete-match-button";
  deleteButton.type = "button";
  deleteButton.textContent = "Excluir";
  deleteButton.setAttribute(
    "aria-label",
    `Excluir ${match.title}, ${displayDate}, às ${match.match_time}, ID ${match.id}`,
  );
  deleteButton.dataset.matchId = String(match.id);

  const actions = document.createElement("div");
  actions.className = "admin-match-actions";
  actions.append(editButton, deleteButton);
  card.append(top, details, actions);
  return card;
}

function formatMatchDate(value) {
  const dateParts = value.split("-");
  return dateParts.length === 3
    ? `${dateParts[2]}/${dateParts[1]}/${dateParts[0]}`
    : value;
}

function showParticipantsLoading() {
  const message = document.createElement("p");
  message.className = "state-message";
  message.textContent = "Carregando participantes...";
  participantsList.replaceChildren(message);
  participantsList.setAttribute("aria-busy", "true");
}

function showParticipantsMessage(title, message, canRetry) {
  const card = document.createElement("div");
  card.className = "state-card";

  const heading = document.createElement("p");
  heading.className = "state-title";
  heading.textContent = title;

  const description = document.createElement("p");
  description.textContent = message;
  card.append(heading, description);

  if (canRetry) {
    const retryButton = document.createElement("button");
    retryButton.className = "retry-button";
    retryButton.type = "button";
    retryButton.textContent = "Tentar novamente";
    retryButton.addEventListener("click", loadParticipants);
    card.append(retryButton);
  }

  participantsList.replaceChildren(card);
  participantsList.setAttribute("aria-busy", "false");
}

function isValidParticipantsResponse(data, requestedMatchId) {
  return (
    data !== null &&
    typeof data === "object" &&
    !Array.isArray(data) &&
    Number.isInteger(data.partida_id) &&
    data.partida_id === requestedMatchId &&
    Array.isArray(data.participantes) &&
    data.participantes.every((name) => typeof name === "string" && name.trim().length > 0)
  );
}

function openParticipants(match, trigger) {
  participantsRequestId += 1;
  selectedParticipantsMatchId = match.id;
  returnFocusToParticipants = trigger;
  participantsSchedule.textContent = `Horário ${match.match_time}`;
  participantsDialog.showModal();
  participantsTitle.focus();
  loadParticipants();
}

async function loadParticipants() {
  const matchId = selectedParticipantsMatchId;
  if (matchId === null || !participantsDialog.open) {
    return;
  }

  const requestId = ++participantsRequestId;
  showParticipantsLoading();

  try {
    const response = await fetch(`/api/v2/partidas/${encodeURIComponent(matchId)}/participantes`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error("Falha ao carregar participantes");
    }

    const data = await response.json();
    if (!isValidParticipantsResponse(data, matchId)) {
      throw new Error("Resposta inválida");
    }

    if (
      requestId !== participantsRequestId ||
      matchId !== selectedParticipantsMatchId ||
      !participantsDialog.open
    ) {
      return;
    }

    if (data.participantes.length === 0) {
      showParticipantsMessage(
        "Nenhum participante confirmado",
        "Não há participantes confirmados para esta partida.",
        false,
      );
      return;
    }

    const list = document.createElement("ul");
    list.className = "participants-names";
    for (const name of data.participantes) {
      const item = document.createElement("li");
      item.textContent = name;
      list.append(item);
    }
    participantsList.replaceChildren(list);
    participantsList.setAttribute("aria-busy", "false");
  } catch {
    if (
      requestId !== participantsRequestId ||
      matchId !== selectedParticipantsMatchId ||
      !participantsDialog.open
    ) {
      return;
    }
    showParticipantsMessage(
      "Não foi possível carregar os participantes",
      "Tente novamente. A falha de consulta não significa que a lista esteja vazia.",
      true,
    );
  }
}

function setBookingFeedback(message, isError = false) {
  formFeedback.textContent = message;
  formFeedback.classList.toggle("form-feedback-error", isError);
}

function setReservationsFeedback(message, isError = false) {
  reservationsFeedback.textContent = message;
  reservationsFeedback.classList.toggle("form-feedback-error", isError);
}

function checkSessionStorage() {
  try {
    const storage = window.sessionStorage;
    const probeKey = `${RESERVATION_STORAGE_KEY}.probe`;
    storage.setItem(probeKey, "1");
    storage.removeItem(probeKey);
    return true;
  } catch {
    return false;
  }
}

function readReservationRecords() {
  const stored = window.sessionStorage.getItem(RESERVATION_STORAGE_KEY);
  if (stored === null) {
    return [];
  }

  const records = JSON.parse(stored);
  const valid = Array.isArray(records) && records.every((record) => (
    record !== null &&
    typeof record === "object" &&
    record.version === RESERVATION_STORAGE_VERSION &&
    Number.isSafeInteger(record.match_id) &&
    record.match_id > 0 &&
    typeof record.recovery_secret === "string" &&
    /^[A-Za-z0-9_-]{43}$/.test(record.recovery_secret) &&
    ["pending", "uncertain", "waiting", "unavailable", "created"].includes(
      record.state,
    ) &&
    (
      record.retry_at === null ||
      (
        Number.isFinite(record.retry_at) &&
        Number.isFinite(new Date(record.retry_at).getTime())
      )
    ) &&
    (record.state !== "waiting" || Number.isFinite(record.retry_at)) &&
    (record.retry_action === null ||
      record.retry_action === "create" ||
      record.retry_action === "recover") &&
    (
      record.payload === undefined ||
      (
        record.payload !== null &&
        typeof record.payload === "object" &&
        Number.isSafeInteger(record.payload.match_id) &&
        record.payload.match_id === record.match_id &&
        typeof record.payload.player_name === "string" &&
        record.payload.player_name.length <= 120 &&
        typeof record.payload.player_phone === "string" &&
        /^\d{10,24}$/.test(record.payload.player_phone) &&
        typeof record.idempotency_key === "string" &&
        /^[A-Za-z0-9_-]{43}$/.test(record.idempotency_key)
      )
    )
  ));
  if (!valid) {
    throw new Error("Stored reservation state is invalid.");
  }
  return records;
}

function persistReservationRecords() {
  try {
    window.sessionStorage.setItem(
      RESERVATION_STORAGE_KEY,
      JSON.stringify(reservationRecords),
    );
    return true;
  } catch {
    return false;
  }
}

function removeReservationRecord(record) {
  reservationRecords = reservationRecords.filter(
    (storedRecord) => storedRecord.recovery_secret !== record.recovery_secret,
  );
  const removed = persistReservationRecords();
  renderSavedReservations();
  return removed;
}

function persistAfterReservationSuccess() {
  if (persistReservationRecords()) {
    return true;
  }
  try {
    window.sessionStorage.removeItem(RESERVATION_STORAGE_KEY);
  } catch {
    return false;
  }
  return reservationRecords.length === 0 || persistReservationRecords();
}

function generateReservationCredential() {
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return window.btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function canCreateReservation() {
  return (
    reservationHttpEnabled &&
    reservationCryptoAvailable &&
    reservationStorageAvailable &&
    reservationRecordsReadable
  );
}

function updateReservationAvailability() {
  if (!reservationHttpEnabled) {
    reservationFeatureStatus.textContent =
      "Reservas temporárias indisponíveis neste ambiente.";
  } else if (!reservationStorageAvailable || !reservationRecordsReadable) {
    reservationFeatureStatus.textContent =
      "O armazenamento desta sessão não está disponível. Nenhuma reserva será enviada.";
  } else if (!reservationCryptoAvailable) {
    reservationFeatureStatus.textContent =
      "Este navegador não oferece geração segura de credenciais. Nenhuma reserva será enviada.";
  } else {
    reservationFeatureStatus.textContent = "";
  }

  createReservationButton.disabled =
    !canCreateReservation() || reservationRequestActive || bookingSucceeded;
  const canRecoverReservations =
    reservationHttpEnabled &&
    reservationStorageAvailable &&
    reservationRecordsReadable;
  navReservationsButton.disabled = !canRecoverReservations;
  navReservationsButton.setAttribute(
    "aria-label",
    canRecoverReservations ? "Minhas reservas nesta sessão" : "Reservas, indisponível",
  );
  const navStatus = navReservationsButton.querySelector("small");
  if (navStatus) {
    navStatus.textContent = canRecoverReservations ? "Sessão" : "Indisponível";
  }
  closeDialogButton.disabled = reservationRequestActive;
}

function formatReservationDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "prazo informado pelo servidor"
    : date.toLocaleString("pt-BR");
}

function isValidReservationResult(result) {
  return (
    result !== null &&
    typeof result === "object" &&
    typeof result.status === "string" &&
    result.status.length > 0 &&
    result.status.length <= 32 &&
    typeof result.locked_until === "string" &&
    Number.isFinite(Date.parse(result.locked_until)) &&
    Number.isSafeInteger(result.price_per_slot_cents) &&
    result.price_per_slot_cents >= 0
  );
}

async function fetchReservation(path, options) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    RESERVATION_TIMEOUT_MS,
  );
  try {
    const response = await fetch(path, {
      ...options,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
    });
    const result = await response.json().catch(() => null);
    return { response, result };
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function withReservationLock(action) {
  if (reservationRequestActive) {
    return false;
  }

  const run = async () => {
    reservationRequestActive = true;
    bookingForm.setAttribute("aria-busy", "true");
    try {
      updateReservationAvailability();
      renderSavedReservations();
      await action();
    } finally {
      reservationRequestActive = false;
      bookingForm.setAttribute("aria-busy", "false");
      updateReservationAvailability();
      renderSavedReservations();
    }
  };

  if (navigator.locks && typeof navigator.locks.request === "function") {
    return navigator.locks.request(
      "pelada-reservation-http",
      { mode: "exclusive", ifAvailable: true },
      async (lock) => {
        if (!lock) {
          return false;
        }
        await run();
        return true;
      },
    );
  }

  await run();
  return true;
}

function parseRetryAfter(response) {
  const header = response.headers.get("Retry-After");
  if (header === null || !/^\d+$/.test(header.trim())) {
    return null;
  }
  const seconds = Number(header);
  if (!Number.isSafeInteger(seconds) || seconds < 1 || seconds > 86_400) {
    return null;
  }
  return Date.now() + seconds * 1000;
}

function scheduleReservationRetry(record, response, action) {
  const retryAt = parseRetryAfter(response);
  record.state = retryAt === null ? "unavailable" : "waiting";
  record.retry_at = retryAt;
  record.retry_action = retryAt === null ? null : action;
  persistReservationRecords();
  renderSavedReservations();
  return retryAt !== null;
}

function setRetryableError(record, message) {
  record.state = "uncertain";
  record.retry_at = null;
  record.retry_action = "create";
  persistReservationRecords();
  renderSavedReservations();
  setBookingFeedback(message, true);
  setReservationsFeedback(message, true);
}

function discardFailedCreation(record, message) {
  const removed = removeReservationRecord(record);
  const finalMessage = removed
    ? message
    : `${message} Não foi possível atualizar o armazenamento local desta sessão.`;
  setBookingFeedback(finalMessage, true);
  setReservationsFeedback(finalMessage, true);
}

function renderSavedReservations() {
  if (reservationCountdownTimer !== null) {
    window.clearTimeout(reservationCountdownTimer);
    reservationCountdownTimer = null;
  }

  const content = document.createDocumentFragment();
  let nextRetryAt = null;
  if (reservationRecords.length === 0) {
    const empty = document.createElement("p");
    empty.className = "state-message";
    empty.textContent = "Nenhuma reserva foi guardada nesta sessão.";
    content.append(empty);
    savedReservationsContainer.replaceChildren(content);
    savedReservationsContainer.setAttribute("aria-busy", "false");
    return;
  }

  for (const record of reservationRecords) {
    const card = document.createElement("article");
    card.className = "match-card saved-reservation-card";

    const title = document.createElement("h2");
    title.className = "saved-reservation-title";
    const match = currentMatches.get(record.match_id);
    title.textContent = match
      ? `${match.title} — ${formatMatchDate(match.match_date)} às ${match.match_time}`
      : "Partida selecionada";

    const retryAt = Number.isFinite(record.retry_at) ? record.retry_at : null;
    const waiting = retryAt !== null && retryAt > Date.now();
    const status = document.createElement("p");
    status.className = "saved-reservation-status";
    if (record.state === "created" && record.status === "bloqueada") {
      status.textContent =
        `Bloqueio temporário. Prazo informado: ${formatReservationDate(record.locked_until)}.`;
    } else if (record.state === "created") {
      status.textContent =
        `Estado informado pelo servidor. Prazo: ${formatReservationDate(record.locked_until)}.`;
    } else if (record.state === "waiting") {
      status.textContent = waiting
        ? `Aguarde até ${formatReservationDate(new Date(retryAt).toISOString())} para repetir.`
        : "O prazo de espera terminou. Você pode repetir manualmente.";
    } else if (record.state === "unavailable") {
      status.textContent =
        "Serviço indisponível. Nenhuma repetição automática foi feita.";
    } else {
      status.textContent =
        "O resultado do pedido é incerto. Use a operação salva para repetir com segurança.";
    }

    const actions = document.createElement("div");
    actions.className = "saved-reservation-actions";

    if (
      record.payload !== undefined &&
      record.idempotency_key &&
      (record.state === "pending" ||
        record.state === "uncertain" ||
        (record.state === "waiting" && record.retry_action === "create"))
    ) {
      const retryButton = document.createElement("button");
      retryButton.className = "retry-button";
      retryButton.type = "button";
      retryButton.textContent = waiting
        ? "Aguardar prazo para repetir"
        : "Repetir envio seguro";
      retryButton.setAttribute(
        "aria-label",
        `Repetir operação salva para ${title.textContent}`,
      );
      retryButton.disabled =
        waiting || reservationRequestActive || !canCreateReservation();
      retryButton.addEventListener("click", () => retrySavedCreation(record));
      actions.append(retryButton);
    }

    if (record.recovery_secret) {
      const recoverButton = document.createElement("button");
      recoverButton.className = "retry-button";
      recoverButton.type = "button";
      recoverButton.textContent =
        waiting && record.state === "waiting" && record.retry_action === "recover"
          ? "Aguardar prazo para consultar"
          : "Consultar estado";
      recoverButton.setAttribute(
        "aria-label",
        `Consultar estado de ${title.textContent}`,
      );
      recoverButton.disabled =
        reservationRequestActive ||
        waiting ||
        !reservationHttpEnabled ||
        !reservationStorageAvailable ||
        !reservationRecordsReadable;
      recoverButton.addEventListener("click", () => recoverSavedReservation(record));
      actions.append(recoverButton);
    }

    card.append(title, status, actions);
    content.append(card);

    if (waiting && (nextRetryAt === null || retryAt < nextRetryAt)) {
      nextRetryAt = retryAt;
    }
  }

  savedReservationsContainer.replaceChildren(content);
  savedReservationsContainer.setAttribute("aria-busy", "false");
  if (nextRetryAt !== null) {
    reservationCountdownTimer = window.setTimeout(
      renderSavedReservations,
      Math.min(nextRetryAt - Date.now(), 2_147_000_000),
    );
  }
}

async function retrySavedCreation(record) {
  if (
    !canCreateReservation() ||
    !record.payload ||
    !record.idempotency_key ||
    (Number.isFinite(record.retry_at) && record.retry_at > Date.now())
  ) {
    return;
  }
  setReservationsFeedback("");
  try {
    const acquired = await withReservationLock(() => sendReservationCreation(record));
    if (!acquired) {
      setReservationsFeedback(
        "Há uma solicitação ativa em outra aba. Aguarde antes de repetir.",
        true,
      );
    }
  } catch {
    setReservationsFeedback(
      "Não foi possível iniciar a repetição com segurança. A operação continua guardada.",
      true,
    );
  }
}

function forgetExpiredReservation(record) {
  const removed = removeReservationRecord(record);
  const message = removed
    ? "O servidor confirmou que o bloqueio expirou. Os dados locais da operação foram removidos."
    : "O servidor confirmou a expiração, mas o armazenamento local não pôde ser atualizado.";
  setReservationsFeedback(message, !removed);
  setBookingFeedback(message, !removed);
}

async function recoverSavedReservation(record) {
  if (
    !reservationHttpEnabled ||
    !reservationStorageAvailable ||
    !reservationRecordsReadable ||
    reservationRequestActive ||
    (Number.isFinite(record.retry_at) && record.retry_at > Date.now())
  ) {
    return;
  }

  setReservationsFeedback("");
  try {
    const acquired = await withReservationLock(async () => {
      let response;
      let result;
      try {
        const reply = await fetchReservation(RECOVER_RESERVATION_PATH, {
          method: "GET",
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${record.recovery_secret}`,
          },
        });
        response = reply.response;
        result = reply.result;
      } catch {
        setReservationsFeedback(
          "Não foi possível consultar o estado. Você pode tentar novamente manualmente.",
          true,
        );
        return;
      }

      if (response.status === 200 && isValidReservationResult(result)) {
        if (result.status === "expirada") {
          forgetExpiredReservation(record);
          return;
        }
        record.state = "created";
        record.status = result.status;
        record.locked_until = result.locked_until;
        record.price_per_slot_cents = result.price_per_slot_cents;
        record.retry_at = null;
        record.retry_action = null;
        persistReservationRecords();
        renderSavedReservations();
        setReservationsFeedback(
          result.status === "bloqueada"
            ? `Bloqueio temporário ativo até ${formatReservationDate(result.locked_until)}.`
            : `Estado consultado. Prazo informado: ${formatReservationDate(result.locked_until)}.`,
        );
        return;
      }

      if (response.status === 404) {
        setReservationsFeedback(
          "Não foi possível localizar uma operação com os dados guardados nesta sessão.",
          true,
        );
        return;
      }
      if (response.status === 429 || response.status === 503) {
        const retryScheduled = scheduleReservationRetry(record, response, "recover");
        setReservationsFeedback(
          retryScheduled
            ? response.status === 429
              ? "Limite temporário atingido. Aguarde o prazo antes de consultar novamente."
              : "O serviço está temporariamente indisponível. Aguarde o prazo antes de consultar novamente."
            : "O serviço está temporariamente indisponível. Nenhuma repetição automática foi feita.",
          true,
        );
        return;
      }
      setReservationsFeedback(
        "Não foi possível consultar o estado. Nenhum detalhe interno foi exibido.",
        true,
      );
    });
    if (!acquired) {
      setReservationsFeedback(
        "Há uma solicitação ativa em outra aba. Aguarde antes de consultar.",
        true,
      );
    }
  } catch {
    setReservationsFeedback(
      "Não foi possível iniciar a consulta com segurança.",
      true,
    );
  }
}

async function initializeReservationInterface() {
  reservationCryptoAvailable =
    Boolean(window.crypto) &&
    typeof window.crypto.getRandomValues === "function" &&
    typeof window.btoa === "function" &&
    typeof window.TextEncoder === "function" &&
    typeof window.AbortController === "function";
  reservationStorageAvailable = checkSessionStorage();
  if (reservationStorageAvailable) {
    try {
      reservationRecords = readReservationRecords();
    } catch {
      reservationRecordsReadable = false;
    }
  }

  try {
    const reply = await fetchReservation(RESERVATION_CAPABILITIES_PATH, {
      headers: { Accept: "application/json" },
    });
    const response = reply.response;
    const capabilities = reply.result;
    if (
      !response.ok ||
      capabilities === null ||
      typeof capabilities !== "object" ||
      typeof capabilities.reservation_http_enabled !== "boolean"
    ) {
      throw new Error("Reservation capability is unavailable.");
    }
    reservationHttpEnabled = capabilities.reservation_http_enabled;
  } catch {
    reservationHttpEnabled = false;
    reservationFeatureStatus.textContent =
      "Não foi possível confirmar a disponibilidade. Nenhuma reserva será enviada.";
  }

  updateReservationAvailability();
  renderSavedReservations();
}

function setFieldError(input, errorElement, message) {
  errorElement.textContent = message;
  input.setAttribute("aria-invalid", String(message.length > 0));
}

function validateBookingForm() {
  const name = playerNameInput.value.trim();
  const phoneDigits = playerPhoneInput.value.replace(/\D/g, "");
  const nameMessage = !name
    ? "Informe seu nome completo."
    : name.length > 120
      ? "O nome deve ter até 120 caracteres."
      : "";
  let phoneMessage = "";

  if (!phoneDigits) {
    phoneMessage = "Informe seu WhatsApp com DDD.";
  } else if (phoneDigits.length !== 10 && phoneDigits.length !== 11) {
    phoneMessage = "Informe DDD e telefone com 10 ou 11 dígitos.";
  } else if (phoneDigits.startsWith("0")) {
    phoneMessage = "Informe um DDD válido.";
  }

  setFieldError(playerNameInput, playerNameError, nameMessage);
  setFieldError(playerPhoneInput, playerPhoneError, phoneMessage);

  const isValid =
    !nameMessage &&
    !phoneMessage &&
    Number.isSafeInteger(selectedBookingMatchId) &&
    selectedBookingMatchId > 0;
  if (!isValid && !nameMessage && !phoneMessage) {
    setBookingFeedback("Selecione novamente uma partida.", true);
  } else if (!isValid) {
    setBookingFeedback("");
  } else {
    setBookingFeedback("");
  }

  if (!isValid) {
    if (nameMessage) {
      playerNameInput.focus();
    } else if (phoneMessage) {
      playerPhoneInput.focus();
    }
  }
  return isValid;
}

async function sendReservationCreation(record) {
  let response;
  let result;
  try {
    const reply = await fetchReservation(CREATE_RESERVATION_PATH, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "Idempotency-Key": record.idempotency_key,
        Authorization: `Bearer ${record.recovery_secret}`,
      },
      body: JSON.stringify(record.payload),
    });
    response = reply.response;
    result = reply.result;
  } catch {
    setRetryableError(
      record,
      "Não foi possível confirmar a resposta. Repita manualmente a operação salva; os mesmos dados serão usados.",
    );
    return;
  }

  if (response.status === 200) {
    if (!isValidReservationResult(result)) {
      setRetryableError(
        record,
        "A resposta não pôde ser confirmada. Repita manualmente a operação salva.",
      );
      return;
    }
    if (result.status === "expirada") {
      bookingSucceeded = true;
      playerNameInput.value = "";
      playerPhoneInput.value = "";
      setFieldError(playerNameInput, playerNameError, "");
      setFieldError(playerPhoneInput, playerPhoneError, "");
      forgetExpiredReservation(record);
      updateReservationAvailability();
      return;
    }

    record.state = "created";
    record.status = result.status;
    record.locked_until = result.locked_until;
    record.price_per_slot_cents = result.price_per_slot_cents;
    record.retry_at = null;
    record.retry_action = null;
    delete record.idempotency_key;
    delete record.payload;
    bookingSucceeded = true;
    playerNameInput.value = "";
    playerPhoneInput.value = "";
    setFieldError(playerNameInput, playerNameError, "");
    setFieldError(playerPhoneInput, playerPhoneError, "");
    updateReservationAvailability();
    if (!persistAfterReservationSuccess()) {
      setBookingFeedback(
        "O servidor respondeu, mas não foi possível atualizar o armazenamento da sessão. Consulte o estado em Minhas reservas.",
        true,
      );
    } else {
      setBookingFeedback(
        `Bloqueio temporário solicitado. Consulte o estado até ${formatReservationDate(result.locked_until)}.`,
      );
    }
    setReservationsFeedback("");
    renderSavedReservations();
    return;
  }

  const code = result?.error?.code;
  if (response.status === 429 || response.status === 503) {
    const retryScheduled = scheduleReservationRetry(record, response, "create");
    const message = retryScheduled
      ? response.status === 429
        ? "Limite temporário atingido. Aguarde o prazo indicado e repita a mesma operação."
        : "O serviço está temporariamente indisponível. Aguarde o prazo antes de repetir."
      : "O serviço está temporariamente indisponível. Nenhuma repetição automática foi feita.";
    setBookingFeedback(message, true);
    setReservationsFeedback(message, true);
    return;
  }
  if (response.status === 500) {
    setRetryableError(
      record,
      "O resultado não pôde ser confirmado. Você pode repetir manualmente a operação salva.",
    );
    return;
  }
  if (response.status === 404 && code === "match_not_found") {
    discardFailedCreation(
      record,
      "A partida não está mais disponível. Atualize a lista antes de tentar novamente.",
    );
    return;
  }
  if (response.status === 409) {
    const message = code === "no_vacancy"
      ? "Não há mais vaga nesta partida."
      : code === "match_unavailable"
        ? "Esta partida não está disponível para bloqueio."
        : "Não foi possível confirmar esta solicitação. Nenhuma nova operação foi criada.";
    discardFailedCreation(record, message);
    return;
  }
  if (response.status === 422) {
    discardFailedCreation(record, "Confira o nome e o telefone e tente novamente.");
    return;
  }
  if ([403, 413, 415, 431].includes(response.status)) {
    discardFailedCreation(
      record,
      "A solicitação não pôde ser aceita neste ambiente. Nenhum detalhe interno foi exibido.",
    );
    return;
  }

  setRetryableError(
    record,
    "Não foi possível confirmar o resultado. Repita manualmente usando a operação salva.",
  );
}

async function submitBooking(event) {
  event.preventDefault();
  validationAttempted = true;
  if (!validateBookingForm() || !canCreateReservation() || reservationRequestActive) {
    return;
  }

  const existingPending = reservationRecords.find(
    (record) =>
      record.match_id === selectedBookingMatchId &&
      record.payload !== undefined,
  );
  if (existingPending) {
    setBookingFeedback(
      "Já existe um pedido não confirmado para esta partida. Consulte Minhas reservas e repita a operação salva.",
      true,
    );
    return;
  }

  const payload = {
    match_id: selectedBookingMatchId,
    player_name: playerNameInput.value.trim(),
    player_phone: playerPhoneInput.value.replace(/\D/g, ""),
  };
  if (new TextEncoder().encode(JSON.stringify(payload)).length >= 4096) {
    setBookingFeedback("A solicitação excede o tamanho permitido.", true);
    return;
  }

  try {
    const acquired = await withReservationLock(async () => {
      const record = {
        version: RESERVATION_STORAGE_VERSION,
        match_id: payload.match_id,
        idempotency_key: generateReservationCredential(),
        recovery_secret: generateReservationCredential(),
        payload,
        state: "pending",
        retry_at: null,
        retry_action: "create",
        status: null,
        locked_until: null,
        price_per_slot_cents: null,
      };
      reservationRecords.push(record);
      if (!persistReservationRecords()) {
        reservationRecords.pop();
        setBookingFeedback(
          "Não foi possível guardar os dados nesta sessão. Por segurança, nada foi enviado.",
          true,
        );
        return;
      }

      renderSavedReservations();
      setBookingFeedback("Enviando solicitação de bloqueio temporário.");
      await sendReservationCreation(record);
    });
    if (!acquired) {
      setBookingFeedback(
        "Há uma solicitação ativa em outra aba. Aguarde e tente novamente.",
        true,
      );
    }
  } catch {
    setBookingFeedback(
      "Não foi possível iniciar a solicitação com segurança. Nada foi enviado.",
      true,
    );
  }
}

function openBookingForm(match, trigger) {
  returnFocusTo = trigger;
  selectedBookingMatchId = match.id;
  validationAttempted = false;
  bookingSucceeded = false;

  selectedSchedule.textContent = match.match_time;
  selectedPrice.textContent = currencyFormatter.format(match.price_per_slot_cents / 100);
  selectedAvailability.textContent =
    `${numberFormatter.format(match.vagas_livres)} de ` +
    `${numberFormatter.format(match.max_players)} vagas livres`;
  playerNameInput.value = "";
  playerPhoneInput.value = "";
  setBookingFeedback("");
  setFieldError(playerNameInput, playerNameError, "");
  setFieldError(playerPhoneInput, playerPhoneError, "");
  updateReservationAvailability();

  bookingDialog.showModal();
  bookingTitle.focus();
}

function closeBookingForm() {
  if (bookingDialog.open && !reservationRequestActive) {
    bookingDialog.close();
  }
}

function resetBookingForm() {
  selectedBookingMatchId = null;
  playerNameInput.value = "";
  playerPhoneInput.value = "";
  setBookingFeedback("");
  setFieldError(playerNameInput, playerNameError, "");
  setFieldError(playerPhoneInput, playerPhoneError, "");
  validationAttempted = false;
  bookingSucceeded = false;
  updateReservationAvailability();
}

closeDialogButton.addEventListener("click", closeBookingForm);
bookingDialog.addEventListener("click", (event) => {
  if (event.target === bookingDialog) {
    closeBookingForm();
  }
});
bookingDialog.addEventListener("close", () => {
  resetBookingForm();
  if (returnFocusTo?.isConnected) {
    returnFocusTo.focus();
  } else {
    document.querySelector("#matches-heading").focus();
  }
  returnFocusTo = null;
});

closeParticipantsButton.addEventListener("click", () => participantsDialog.close());
participantsDialog.addEventListener("click", (event) => {
  if (event.target === participantsDialog) {
    participantsDialog.close();
  }
});
participantsDialog.addEventListener("close", () => {
  participantsRequestId += 1;
  selectedParticipantsMatchId = null;
  participantsList.replaceChildren();
  participantsList.setAttribute("aria-busy", "false");

  if (returnFocusToParticipants?.isConnected) {
    returnFocusToParticipants.focus();
  } else {
    document.querySelector("#matches-heading").focus();
  }
  returnFocusToParticipants = null;
});

function updateValidationAfterInput() {
  if (validationAttempted) {
    validateBookingForm();
  }
}

playerNameInput.addEventListener("input", updateValidationAfterInput);
playerPhoneInput.addEventListener("input", updateValidationAfterInput);
bookingForm.addEventListener("submit", submitBooking);

function showAdminLoading() {
  currentAdminMatches = new Map();
  const skeletons = document.createDocumentFragment();
  for (let index = 0; index < 2; index += 1) {
    const skeleton = document.createElement("div");
    skeleton.className = "skeleton";
    skeleton.setAttribute("aria-hidden", "true");
    skeletons.append(skeleton);
  }
  adminMatchesContainer.replaceChildren(skeletons);
  adminMatchesContainer.setAttribute("aria-busy", "true");
}

async function fetchMatchData() {
  const response = await fetch("/api/v2/partidas", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("Falha ao carregar partidas");
  }

  const partidas = await response.json();
  if (!Array.isArray(partidas) || !partidas.every(isValidMatch)) {
    throw new Error("Resposta inválida");
  }
  return partidas;
}

function renderAdminMatches(partidas) {
  if (partidas.length === 0) {
    showAdminMessage(
      "Nenhuma partida cadastrada",
      "Cadastre uma partida para que ela apareça nesta lista.",
      true,
    );
    return;
  }

  currentAdminMatches = new Map(partidas.map((partida) => [partida.id, partida]));
  const cards = document.createDocumentFragment();
  for (const partida of partidas) {
    cards.append(createAdminMatchCard(partida));
  }
  adminMatchesContainer.replaceChildren(cards);
  adminMatchesContainer.setAttribute("aria-busy", "false");
}

async function refreshMatchLists() {
  showLoading();
  showAdminLoading();
  matchesFeedback.textContent = "";

  try {
    const partidas = await fetchMatchData();
    currentMatches = new Map(partidas.map((partida) => [partida.id, partida]));
    if (partidas.length === 0) {
      showMessage(
        "Nenhuma partida disponível",
        "Não há partidas para exibir agora.",
        true,
      );
    } else {
      const cards = document.createDocumentFragment();
      for (const partida of partidas) {
        cards.append(createMatchCard(partida));
      }
      matchesContainer.replaceChildren(cards);
      matchesContainer.setAttribute("aria-busy", "false");
    }

    renderAdminMatches(partidas);
    renderSavedReservations();
    return true;
  } catch {
    showMessage(
      "Não foi possível carregar as partidas",
      "Verifique se os servidores locais estão disponíveis e tente novamente.",
      true,
    );
    showAdminMessage(
      "Não foi possível carregar as partidas",
      "Verifique se o servidor local está disponível e tente novamente.",
      true,
    );
    return false;
  }
}

async function loadMatches() {
  return refreshMatchLists();
}

async function loadAdminMatches() {
  return refreshMatchLists();
}

function parsePriceToCents(value) {
  const match = /^(\d+)(?:[,.](\d{1,2}))?$/.exec(value.trim());
  if (!match) {
    return null;
  }

  const cents = BigInt(match[1]) * 100n + BigInt((match[2] || "").padEnd(2, "0"));
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) {
    return null;
  }
  return Number(cents);
}

function centsToPriceInput(cents) {
  if (!Number.isSafeInteger(cents) || cents < 0) {
    return "";
  }
  const amount = BigInt(cents);
  const reais = amount / 100n;
  const centavos = String(amount % 100n).padStart(2, "0");
  return `${reais},${centavos}`;
}

function isValidDateInput(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

const adminFields = {
  key: {
    input: adminKeyInput,
    error: document.querySelector("#admin-key-error"),
  },
  title: {
    input: document.querySelector("#match-title"),
    error: document.querySelector("#match-title-error"),
  },
  date: {
    input: document.querySelector("#match-date"),
    error: document.querySelector("#match-date-error"),
  },
  time: {
    input: document.querySelector("#match-time"),
    error: document.querySelector("#match-time-error"),
  },
  location: {
    input: document.querySelector("#match-location"),
    error: document.querySelector("#match-location-error"),
  },
  duration: {
    input: document.querySelector("#match-duration"),
    error: document.querySelector("#match-duration-error"),
  },
  players: {
    input: document.querySelector("#match-players"),
    error: document.querySelector("#match-players-error"),
  },
  price: {
    input: document.querySelector("#match-price"),
    error: document.querySelector("#match-price-error"),
  },
};

function validateAdminForm(focusInvalid = false) {
  const errors = [];
  const title = adminFields.title.input.value.trim();
  const matchDate = adminFields.date.input.value;
  const matchTime = adminFields.time.input.value;
  const location = adminFields.location.input.value.trim();
  const durationText = adminFields.duration.input.value;
  const duration = /^\d+$/.test(durationText) ? Number(durationText) : NaN;
  const maxPlayers = Number(adminFields.players.input.value);
  const priceCents = parsePriceToCents(adminFields.price.input.value);

  const messages = {
    key: adminKeyInMemory ? "" : "Informe a chave administrativa.",
    title: title ? "" : "Informe o título da partida.",
    date: isValidDateInput(matchDate) ? "" : "Informe uma data válida.",
    time: /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(matchTime)
      ? ""
      : "Informe um horário válido.",
    location: location ? "" : "Informe o local.",
    duration:
      Number.isSafeInteger(duration) && duration > 0
        ? ""
        : "Informe a duração em minutos, usando um inteiro positivo.",
    players: maxPlayers === 10 || maxPlayers === 12 || maxPlayers === 14
      ? ""
      : "Escolha 10, 12 ou 14 jogadores.",
    price: priceCents === null
      ? "Informe um preço válido com até duas casas decimais."
      : "",
  };

  for (const [name, field] of Object.entries(adminFields)) {
    const message = messages[name];
    setFieldError(field.input, field.error, message);
    if (message) {
      errors.push(field.input);
    }
  }

  if (errors.length > 0) {
    if (focusInvalid) {
      errors[0].focus();
    }
    return null;
  }

  return {
    title,
    match_date: matchDate,
    match_time: matchTime,
    location,
    duration_minutes: duration,
    max_players: maxPlayers,
    price_per_slot_cents: priceCents,
  };
}

function clearAdminKey() {
  adminKeyInMemory = "";
  adminKeyInput.value = "";
  setFieldError(adminFields.key.input, adminFields.key.error, "");
}

function setAdminFeedback(message, isError = false) {
  adminFeedback.textContent = message;
  adminFeedback.classList.toggle("form-feedback-error", isError);
}

function setDeleteFeedback(message, isError = false) {
  deleteMatchFeedback.textContent = message;
  deleteMatchFeedback.classList.toggle("form-feedback-error", isError);
}

function openDeleteConfirmation(match, trigger) {
  if (deletePending) {
    return;
  }
  pendingDeleteMatchId = match.id;
  returnFocusToDelete = trigger;
  deleteMatchSummaryTitle.textContent = match.title;
  deleteMatchSummaryDate.textContent = formatMatchDate(match.match_date);
  deleteMatchSummaryTime.textContent = match.match_time;
  deleteMatchSummaryId.textContent = String(match.id);
  deleteNeedsRefresh = false;
  refreshAfterDeleteButton.hidden = true;
  confirmDeleteMatchButton.disabled = false;
  cancelDeleteMatchButton.disabled = false;
  setDeleteFeedback("");
  deleteMatchDialog.showModal();
  deleteMatchTitle.focus();
}

function setDeletePending(pending) {
  deletePending = pending;
  deleteMatchDialog.setAttribute("aria-busy", String(pending));
  confirmDeleteMatchButton.disabled = pending || deleteNeedsRefresh;
  cancelDeleteMatchButton.disabled = pending;
  refreshAfterDeleteButton.disabled = pending;
  for (const button of adminMatchesContainer.querySelectorAll(
    ".edit-match-button, .delete-match-button",
  )) {
    button.disabled = pending;
  }
}

function clearDeletedMatchSelections(matchId) {
  if (editingMatchId === matchId) {
    setCreateMode();
  }
  if (selectedBookingMatchId === matchId) {
    if (bookingDialog.open) {
      closeBookingForm();
    } else {
      resetBookingForm();
    }
  }
  if (selectedParticipantsMatchId === matchId && participantsDialog.open) {
    participantsDialog.close();
  }
}

function closeDeleteConfirmation() {
  if (!deletePending && deleteMatchDialog.open) {
    deleteMatchDialog.close();
  }
}

async function refreshAfterUncertainDelete() {
  if (!deleteNeedsRefresh || deletePending || pendingDeleteMatchId === null) {
    return;
  }
  const matchId = pendingDeleteMatchId;
  setDeletePending(true);
  setDeleteFeedback("Atualizando as listas antes de permitir outra tentativa...");
  try {
    const refreshed = await refreshMatchLists();
    if (!refreshed) {
      setDeleteFeedback(
        "Não foi possível atualizar a listagem. Tente atualizá-la novamente antes de excluir.",
        true,
      );
      return;
    }
    if (!currentAdminMatches.has(matchId)) {
      clearDeletedMatchSelections(matchId);
      setDeletePending(false);
      deleteMatchDialog.close();
      setAdminFeedback(
        "A partida não aparece mais na listagem atualizada. Confira o estado antes de qualquer nova ação.",
        true,
      );
      return;
    }

    deleteNeedsRefresh = false;
    refreshAfterDeleteButton.hidden = true;
    setDeleteFeedback(
      "A partida ainda aparece na listagem. Confira os dados e confirme novamente se deseja excluir.",
    );
  } catch {
    setDeleteFeedback(
      "Não foi possível atualizar a listagem. Tente atualizá-la novamente antes de excluir.",
      true,
    );
  } finally {
    setDeletePending(false);
  }
}

async function confirmMatchDeletion() {
  const matchId = pendingDeleteMatchId;
  if (matchId === null || deletePending || deleteNeedsRefresh) {
    return;
  }
  const requestKey = adminKeyInMemory;
  setDeletePending(true);
  setDeleteFeedback("Excluindo partida...");
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 15000);

  try {
    const response = await fetch(
      `/api/v2/admin/partidas/${encodeURIComponent(matchId)}`,
      {
        method: "DELETE",
        headers: {
          Accept: "application/json",
          "X-Admin-Key": requestKey,
        },
        cache: "no-store",
        signal: controller.signal,
      },
    );

    if (response.status === 401) {
      setFieldError(adminFields.key.input, adminFields.key.error, "Chave administrativa inválida.");
      setDeleteFeedback("Chave administrativa inválida.", true);
      return;
    }
    if (response.status === 404) {
      clearDeletedMatchSelections(matchId);
      await refreshMatchLists();
      setDeletePending(false);
      deleteMatchDialog.close();
      setAdminFeedback("Partida não encontrada. A listagem foi atualizada.", true);
      return;
    }
    if (response.status === 409) {
      setDeleteFeedback(
        "A partida não pode ser excluída: precisa estar agendada e sem reservas ou votos.",
        true,
      );
      return;
    }
    if (response.status !== 204) {
      deleteNeedsRefresh = true;
      refreshAfterDeleteButton.hidden = false;
      setDeleteFeedback(
        "Não foi possível confirmar o resultado. Atualize a listagem antes de tentar novamente.",
        true,
      );
      return;
    }

    clearDeletedMatchSelections(matchId);
    const refreshed = await refreshMatchLists();
    setDeletePending(false);
    deleteMatchDialog.close();
    setAdminFeedback(
      refreshed
        ? "Partida excluída e listas atualizadas."
        : "Partida excluída, mas não foi possível atualizar as listas.",
      !refreshed,
    );
    matchesFeedback.textContent = refreshed
      ? "Partida excluída."
      : "Partida excluída. Não foi possível atualizar a lista; tente novamente.";
  } catch {
    deleteNeedsRefresh = true;
    refreshAfterDeleteButton.hidden = false;
    setDeleteFeedback(
      "O resultado pode ser incerto. Atualize a listagem antes de tentar novamente.",
      true,
    );
  } finally {
    window.clearTimeout(timeoutId);
    setDeletePending(false);
  }
}

function setCreateMode() {
  editingMatchId = null;
  adminFormHeading.textContent = "Cadastrar partida";
  createMatchButton.textContent = "Cadastrar partida";
  cancelEditButton.hidden = true;
  adminFields.title.input.value = "";
  adminFields.date.input.value = "";
  adminFields.time.input.value = "";
  adminFields.location.input.value = "";
  adminFields.duration.input.value = "";
  adminFields.players.input.value = "10";
  adminFields.price.input.value = "";
  clearAdminValidation();
  setAdminFeedback("");
}

function startEditingMatch(match) {
  editingMatchId = match.id;
  adminFields.title.input.value = match.title;
  adminFields.date.input.value = match.match_date;
  adminFields.time.input.value = match.match_time;
  adminFields.location.input.value = match.location;
  adminFields.duration.input.value = String(match.duration_minutes);
  adminFields.players.input.value = String(match.max_players);
  adminFields.price.input.value = centsToPriceInput(match.price_per_slot_cents);
  adminFormHeading.textContent = `Editar partida #${numberFormatter.format(match.id)}`;
  createMatchButton.textContent = "Salvar alterações";
  cancelEditButton.hidden = false;
  clearAdminValidation();
  setAdminFeedback("");
  adminFields.title.input.focus();
}

function setAdminPending(pending) {
  adminFieldset.disabled = pending;
  createMatchButton.disabled = pending;
  cancelEditButton.disabled = pending;
  adminForm.setAttribute("aria-busy", String(pending));
  for (const button of adminMatchesContainer.querySelectorAll(
    ".edit-match-button, .delete-match-button",
  )) {
    button.disabled = pending;
  }
}

function setActiveView(view) {
  const showAdmin = view === "admin";
  const showReservations = view === "reservations";
  matchesPanel.hidden = showAdmin || showReservations;
  reservationsPanel.hidden = !showReservations;
  adminPanel.hidden = !showAdmin;
  navMatchesButton.classList.toggle(
    "nav-item-active",
    !showAdmin && !showReservations,
  );
  navReservationsButton.classList.toggle("nav-item-active", showReservations);
  navAdminButton.classList.toggle("nav-item-active", showAdmin);

  if (showAdmin) {
    navAdminButton.setAttribute("aria-current", "page");
    navMatchesButton.removeAttribute("aria-current");
    navReservationsButton.removeAttribute("aria-current");
    document.querySelector("#admin-heading").focus();
    loadAdminMatches();
  } else if (showReservations) {
    navReservationsButton.setAttribute("aria-current", "page");
    navMatchesButton.removeAttribute("aria-current");
    navAdminButton.removeAttribute("aria-current");
    reservationsHeading.focus();
    renderSavedReservations();
  } else {
    navMatchesButton.setAttribute("aria-current", "page");
    navReservationsButton.removeAttribute("aria-current");
    navAdminButton.removeAttribute("aria-current");
    setCreateMode();
    clearAdminKey();
    document.querySelector("#matches-heading").focus();
  }
}

function clearAdminValidation() {
  for (const field of Object.values(adminFields)) {
    setFieldError(field.input, field.error, "");
  }
  adminValidationAttempted = false;
}

async function submitAdminForm(event) {
  event.preventDefault();
  if (createMatchButton.disabled || adminFieldset.disabled) {
    return;
  }
  adminValidationAttempted = true;
  setAdminFeedback("");
  const payload = validateAdminForm(true);
  if (!payload) {
    return;
  }

  const matchId = editingMatchId;
  const isEditing = matchId !== null;
  const expectedStatus = isEditing ? 200 : 201;
  const endpoint = isEditing
    ? `/api/v2/admin/partidas/${encodeURIComponent(matchId)}`
    : "/api/v2/admin/partidas";
  const requestKey = adminKeyInMemory;

  setAdminPending(true);
  setAdminFeedback(isEditing ? "Salvando alterações..." : "Enviando cadastro...");
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 15000);

  try {
    const response = await fetch(endpoint, {
      method: isEditing ? "PUT" : "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Admin-Key": requestKey,
      },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: controller.signal,
    });

    if (response.status === 401) {
      setFieldError(adminFields.key.input, adminFields.key.error, "Chave administrativa inválida.");
      setAdminFeedback("Chave administrativa inválida.", true);
      return;
    }
    if (isEditing && response.status === 404) {
      await refreshMatchLists();
      setAdminFeedback("Partida não encontrada. A lista foi atualizada.", true);
      return;
    }
    if (isEditing && response.status === 409) {
      setAdminFeedback(
        "Esta partida não pode ser editada: precisa estar agendada e sem nenhuma reserva.",
        true,
      );
      return;
    }
    if (response.status === 422) {
      setAdminFeedback("O servidor rejeitou os dados. Revise os campos.", true);
      return;
    }
    if (response.status !== expectedStatus) {
      setAdminFeedback(
        "Não foi possível confirmar o resultado. Confira os dados da partida antes de tentar novamente.",
        true,
      );
      return;
    }

    const saved = await response.json();
    if (
      !isValidMatch(saved) ||
      saved.price_per_slot_cents !== payload.price_per_slot_cents ||
      (isEditing && saved.id !== matchId)
    ) {
      setAdminFeedback(
        "A resposta foi inesperada. Confira os dados da partida antes de tentar novamente.",
        true,
      );
      return;
    }

    setCreateMode();
    const refreshed = await refreshMatchLists();
    const operation = isEditing ? "atualizada" : "cadastrada";
    setAdminFeedback(
      refreshed
        ? `Partida ${operation} e listas atualizadas.`
        : `Partida ${operation}, mas não foi possível atualizar as listas.`,
    );
    matchesFeedback.textContent = refreshed
      ? `Partida ${operation}.`
      : `Partida ${operation}. Não foi possível atualizar a lista; tente novamente.`;
  } catch {
    setAdminFeedback(
      "Não foi possível confirmar o resultado. Confira os dados da partida antes de tentar novamente.",
      true,
    );
  } finally {
    window.clearTimeout(timeoutId);
    setAdminPending(false);
    if (adminFields.key.error.textContent && !adminPanel.hidden) {
      adminFields.key.input.focus();
    }
  }
}

adminKeyInput.addEventListener("input", () => {
  adminKeyInMemory = adminKeyInput.value;
  if (adminValidationAttempted) {
    validateAdminForm();
  }
});

adminMatchesContainer.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }
  const button = event.target.closest(".edit-match-button, .delete-match-button");
  if (!button || button.disabled) {
    return;
  }

  const matchId = Number(button.dataset.matchId);
  const match = currentAdminMatches.get(matchId);
  if (match && button.classList.contains("edit-match-button")) {
    startEditingMatch(match);
    adminFormHeading.scrollIntoView({ block: "start", behavior: "smooth" });
  } else if (match && button.classList.contains("delete-match-button")) {
    openDeleteConfirmation(match, button);
  }
});

cancelDeleteMatchButton.addEventListener("click", closeDeleteConfirmation);
confirmDeleteMatchButton.addEventListener("click", confirmMatchDeletion);
refreshAfterDeleteButton.addEventListener("click", refreshAfterUncertainDelete);
deleteMatchDialog.addEventListener("cancel", (event) => {
  if (deletePending) {
    event.preventDefault();
  }
});
deleteMatchDialog.addEventListener("click", (event) => {
  if (event.target === deleteMatchDialog) {
    closeDeleteConfirmation();
  }
});
deleteMatchDialog.addEventListener("close", () => {
  pendingDeleteMatchId = null;
  deleteNeedsRefresh = false;
  confirmDeleteMatchButton.disabled = false;
  cancelDeleteMatchButton.disabled = false;
  refreshAfterDeleteButton.hidden = true;
  setDeleteFeedback("");
  if (returnFocusToDelete?.isConnected) {
    returnFocusToDelete.focus();
  } else if (!adminPanel.hidden) {
    document.querySelector("#admin-matches-heading").focus();
  }
  returnFocusToDelete = null;
});

cancelEditButton.addEventListener("click", () => {
  if (cancelEditButton.disabled || editingMatchId === null) {
    return;
  }
  setCreateMode();
  setAdminFeedback("Edição cancelada. Nenhuma alteração foi enviada.");
  adminFormHeading.focus();
});

adminForm.addEventListener("input", () => {
  if (adminValidationAttempted) {
    validateAdminForm();
    if (!createMatchButton.disabled) {
      setAdminFeedback("");
    }
  }
});
adminForm.addEventListener("change", () => {
  if (adminValidationAttempted) {
    validateAdminForm();
    if (!createMatchButton.disabled) {
      setAdminFeedback("");
    }
  }
});
adminForm.addEventListener("submit", submitAdminForm);
navMatchesButton.addEventListener("click", () => setActiveView("matches"));
navReservationsButton.addEventListener(
  "click",
  () => setActiveView("reservations"),
);
navAdminButton.addEventListener("click", () => setActiveView("admin"));

loadMatches();
initializeReservationInterface();
