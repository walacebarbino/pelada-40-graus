const matchesContainer = document.querySelector("#matches");
const matchesPanel = document.querySelector("#matches-panel");
const adminPanel = document.querySelector("#admin-panel");
const matchesFeedback = document.querySelector("#matches-feedback");
const adminMatchesContainer = document.querySelector("#admin-matches");
const navMatchesButton = document.querySelector("#nav-matches");
const navAdminButton = document.querySelector("#nav-admin");
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
const validateBookingButton = document.querySelector("#validate-booking");
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

function setFieldError(input, errorElement, message) {
  errorElement.textContent = message;
  input.setAttribute("aria-invalid", String(message.length > 0));
}

function validateBookingForm() {
  const name = playerNameInput.value.trim();
  const phoneDigits = playerPhoneInput.value.replace(/\D/g, "");
  const nameMessage = name ? "" : "Informe seu nome completo.";
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

  const isValid = !nameMessage && !phoneMessage;
  if (isValid) {
    formFeedback.textContent =
      "Preenchimento válido somente nesta tela. Nada foi enviado ou salvo.";
  } else {
    formFeedback.textContent = "";
    if (nameMessage) {
      playerNameInput.focus();
    } else {
      playerPhoneInput.focus();
    }
  }
  return isValid;
}

function openBookingForm(match, trigger) {
  returnFocusTo = trigger;
  selectedBookingMatchId = match.id;
  validationAttempted = false;

  selectedSchedule.textContent = match.match_time;
  selectedPrice.textContent = currencyFormatter.format(match.price_per_slot_cents / 100);
  selectedAvailability.textContent =
    `${numberFormatter.format(match.vagas_livres)} de ` +
    `${numberFormatter.format(match.max_players)} vagas livres`;
  playerNameInput.value = "";
  playerPhoneInput.value = "";
  formFeedback.textContent = "";
  setFieldError(playerNameInput, playerNameError, "");
  setFieldError(playerPhoneInput, playerPhoneError, "");

  bookingDialog.showModal();
  bookingTitle.focus();
}

function closeBookingForm() {
  if (bookingDialog.open) {
    bookingDialog.close();
  }
}

function resetBookingForm() {
  selectedBookingMatchId = null;
  playerNameInput.value = "";
  playerPhoneInput.value = "";
  formFeedback.textContent = "";
  setFieldError(playerNameInput, playerNameError, "");
  setFieldError(playerPhoneInput, playerPhoneError, "");
  validationAttempted = false;
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
validateBookingButton.addEventListener("click", () => {
  validationAttempted = true;
  validateBookingForm();
});
bookingForm.addEventListener("submit", (event) => {
  event.preventDefault();
  validationAttempted = true;
  validateBookingForm();
});

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
  matchesPanel.hidden = showAdmin;
  adminPanel.hidden = !showAdmin;
  navMatchesButton.classList.toggle("nav-item-active", !showAdmin);
  navAdminButton.classList.toggle("nav-item-active", showAdmin);

  if (showAdmin) {
    navAdminButton.setAttribute("aria-current", "page");
    navMatchesButton.removeAttribute("aria-current");
    document.querySelector("#admin-heading").focus();
    loadAdminMatches();
  } else {
    navMatchesButton.setAttribute("aria-current", "page");
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
navAdminButton.addEventListener("click", () => setActiveView("admin"));

loadMatches();
