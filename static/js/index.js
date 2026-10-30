const fetchBtn = document.getElementById("fetchSchedule");
const container = document.getElementById("schedule-container");
const runBtn = document.getElementById("run-btn");
const loading = document.getElementById("loadingOverlay");
const historyContainer = document.getElementById("schedule-history");

const HISTORY_KEY = "scheduleSearchHistory";
const HISTORY_LIMIT = 5;

function getSearchPayload() {
  return {
    date: document.getElementById("dateDropdown").value,
    time: document.getElementById("time").value,
    from_station: document.getElementById("fromStation").value,
    to_station: document.getElementById("toStation").value
  };
}

function getDateLabel(dateValue) {
  if (!dateValue || dateValue.length !== 8) return dateValue;
  const yyyy = dateValue.slice(0, 4);
  const mm = dateValue.slice(4, 6);
  const dd = dateValue.slice(6, 8);
  return `${yyyy}/${mm}/${dd}`;
}

function makeHistoryLabel(item) {
  return `${item.from_station}→${item.to_station} ${getDateLabel(item.date)} ${item.time}:00`;
}

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(history) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
}

function isSameHistory(a, b) {
  return (
    a.date === b.date &&
    a.time === b.time &&
    a.from_station === b.from_station &&
    a.to_station === b.to_station
  );
}

function addHistory(item) {
  const history = loadHistory().filter(saved => !isSameHistory(saved, item));
  history.unshift(item);
  saveHistory(history.slice(0, HISTORY_LIMIT));
  renderHistoryButtons();
}

function removeHistory(itemToRemove) {
  const history = loadHistory().filter(item => !isSameHistory(item, itemToRemove));
  saveHistory(history);
  renderHistoryButtons();
}

function applyHistory(item) {
  document.getElementById("dateDropdown").value = item.date;
  document.getElementById("time").value = item.time;
  document.getElementById("fromStation").value = item.from_station;
  document.getElementById("toStation").value = item.to_station;
  clearSchedule();
}

function renderHistoryButtons() {
  if (!historyContainer) return;

  const history = loadHistory();
  historyContainer.innerHTML = "";

  if (history.length === 0) return;

  history.forEach((item) => {
    const wrapper = document.createElement("div");
    wrapper.className = "history-chip";

    const applyBtn = document.createElement("button");
    applyBtn.type = "button";
    applyBtn.className = "btn btn-outline-secondary btn-sm history-apply-btn";
    applyBtn.innerText = makeHistoryLabel(item);
    applyBtn.addEventListener("click", () => {
      applyHistory(item);
    });

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn btn-outline-danger btn-sm history-remove-btn";
    removeBtn.innerText = "×";
    removeBtn.title = "히스토리 삭제";
    removeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      removeHistory(item);
    });

    wrapper.appendChild(applyBtn);
    wrapper.appendChild(removeBtn);
    historyContainer.appendChild(wrapper);
  });
}

function showLoading() {
  loading.classList.remove("d-none");
}
function hideLoading() {
  loading.classList.add("d-none");
}

fetchBtn.addEventListener("click", async () => {
  const { date, time, from_station, to_station } = getSearchPayload();
  const login_id = document.getElementById("loginField").value;
  const login_psw = document.getElementById("pwField").value;

  if (!login_id || !login_psw || !date || !time || !from_station || !to_station) {
    return alert("로그인 정보와 날짜·시간·출발역·도착역을 모두 입력해주세요.");
  }

  const params = new URLSearchParams({
    login_id,
    login_psw,
    date,
    time,
    from_station,
    to_station
  });

  showLoading();
  try {
    const res = await fetch("/schedule", {
      method: "POST",
      body: params
    });
    if (!res.ok) {
      let message = res.statusText;
      try {
        const error = await res.json();
        message = error.detail || message;
      } catch {
        // JSON 응답이 아니면 HTTP 상태 메시지를 사용한다.
      }
      throw new Error(message);
    }

    const data = await res.json();
    renderSchedule(data);

    addHistory({
      date,
      time,
      from_station,
      to_station
    });

    container.scrollIntoView({ behavior: "smooth", block: "center" });

  } catch (err) {
    alert("스케줄 조회 중 오류: " + err.message);
  } finally {
    hideLoading();
  }
});

// 조회 조건이 바뀌면 이전 조회의 열차를 새 조건으로 실행하지 않도록 결과를 지운다.
function clearSchedule() {
  container.innerHTML = "";
  runBtn.disabled = true;
}

["dateDropdown", "time", "fromStation", "toStation"].forEach(id => {
  document.getElementById(id).addEventListener("change", clearSchedule);
});

// 좌석 고르기 렌더링
function renderSchedule(data) {
  clearSchedule();
  const table = document.createElement("table");
  table.className = "table table-bordered text-center";

  table.innerHTML = `
    <thead class="table-light">
      <tr>
        <th>열차번호</th><th>출발시간</th><th>도착시간</th><th>예약상태</th>
      </tr>
    </thead>
  `;

  const tbody = document.createElement("tbody");
  data.forEach(item => {
    const tr = document.createElement("tr");

    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.name = "trains";
    chk.value = item.train;
    chk.style.display = "none";

    const tdTrain = document.createElement("td");
    tdTrain.appendChild(chk);
    tdTrain.appendChild(document.createTextNode(item.train));
    tr.appendChild(tdTrain);

    ["depart","arrive"].forEach(key => {
      const td = document.createElement("td");
      td.innerText = item[key];
      tr.appendChild(td);
    });

    const tdStatus = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.innerText = item.status;
    btn.className = "btn btn-sm " +
      (item.status==="매진" ? "btn-secondary" : "btn-success");
    tdStatus.appendChild(btn);
    tr.appendChild(tdStatus);

    tr.addEventListener("click", () => {
      chk.checked = !chk.checked;
      tr.classList.toggle("table-primary", chk.checked);

      const any = Array.from(container.querySelectorAll('input[name="trains"]'))
                       .some(c=>c.checked);

      if (any) {
        runBtn.disabled = false;
      } 
      else {
        runBtn.disabled = true;
      }
    });

    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  container.appendChild(table);
}

// 오늘 기준 한달 렌더링
function populateDateDropdown() {
  const dateDropdown = document.getElementById("dateDropdown");
  if (!dateDropdown) return;

  dateDropdown.innerHTML = "";

  const today = new Date();
  for (let i = 0; i <= 30; i++) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + i);
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const val = `${yyyy}${mm}${dd}`;
    const text = `${yyyy}/${mm}/${dd}(${['일','월','화','수','목','금','토'][d.getDay()]})`;

    const opt = document.createElement('option');
    opt.value = val;
    opt.innerText = text;
    dateDropdown.appendChild(opt);
  }
}
populateDateDropdown();

const reserveSwitch = document.getElementById('reserveSwitch');

// run 호출
document.getElementById('run-btn').addEventListener('click', runMacro);

async function runMacro() {
  const login_id = document.getElementById('loginField').value;
  const login_psw = document.getElementById('pwField').value;
  const from_stn = document.getElementById('fromStation').value;
  const to_stn = document.getElementById('toStation').value;
  const date = document.getElementById('dateDropdown').value;
  const time = document.getElementById('time').value;
  const reserve = reserveSwitch.checked;
  const trains = Array.from(container.querySelectorAll('input[name="trains"]:checked'))
    .map(el => el.value);

  if (![login_id, login_psw, from_stn, to_stn, date, time].every(Boolean) || trains.length === 0) {
    return alert('모든 필드를 채우고 열차를 최소 하나 선택하세요.');
  }

  const params = new URLSearchParams();
  params.append('login_id', login_id);
  params.append('login_psw', login_psw);
  params.append('from_station', from_stn);
  params.append('to_station', to_stn);
  params.append('date', date);
  params.append('time', time);
  params.append('reserve', reserve);
  trains.forEach(train => params.append('trains', train));

  try {
    const res = await fetch('/run', {
      method: 'POST',
      body: params
    });
    if (!res.ok) {
      let message = res.statusText;
      try {
        const error = await res.json();
        message = error.detail || message;
      } catch {
        // JSON 응답이 아니면 HTTP 상태 메시지를 사용한다.
      }
      throw new Error(message);
    }

    triggerAlarmPopup({
      title: '🚀 예매 알림',
      messages: [
        '코레일에 표시된 결제기한 내에 결제하셔야 예매가 확정됩니다.'
      ]
    });
  } catch (err) {
    alert('매크로 실행 중 오류: ' + err.message);
  }
}

// 알람 모달 + 사운드 재생
function triggerAlarmPopup({ title, messages }) {
  const modalEl = document.getElementById('sirenModal');
  const titleEl = document.getElementById('sirenModalLabel');
  const bodyEl  = document.getElementById('sirenModalBody');
  if (!modalEl || !titleEl || !bodyEl) return;

  titleEl.innerText = title;
  bodyEl.innerHTML = messages.map(msg => `<p>${msg}</p>`).join('');

  const audio = new Audio('/static/siren.mp3');
  audio.loop   = true;
  audio.volume = 1.0;
  audio.play().catch(() => {
    alert('브라우저 자동 재생 정책으로 차단되었습니다.\n설정 변경 후 다시 시도해주세요.');
  });

  const sirenModal = new bootstrap.Modal(modalEl);
  sirenModal.show();

  modalEl.addEventListener('hidden.bs.modal', () => {
    audio.pause();
    audio.currentTime = 0;
  }, { once: true });
}

async function updateKakaoConnectionStatus() {
  const statusEl = document.getElementById("kakao-status");
  const loginBtn = document.getElementById("kakao-login-btn");
  const testBtn = document.getElementById("kakao-test-btn");
  const disconnectBtn = document.getElementById("kakao-disconnect-btn");
  if (!statusEl || !loginBtn || !testBtn || !disconnectBtn) return;

  try {
    const response = await fetch("/auth/kakao/status");
    if (!response.ok) throw new Error(response.statusText);
    const { connected } = await response.json();

    statusEl.innerText = connected ? "연결됨" : "연결 안 됨";
    statusEl.className = `badge ${connected ? "text-bg-success" : "text-bg-secondary"}`;
    loginBtn.classList.toggle("d-none", connected);
    testBtn.classList.toggle("d-none", !connected);
    disconnectBtn.classList.toggle("d-none", !connected);
  } catch {
    statusEl.innerText = "연결 상태 확인 실패";
    statusEl.className = "badge text-bg-danger";
  }
}

async function testKakaoMessage() {
  try {
    const response = await fetch("/auth/kakao/test-message", { method: "POST" });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || response.statusText);
    }
    alert("카카오톡 나와의 채팅방으로 테스트 메시지를 보냈습니다.");
  } catch (error) {
    alert("카카오톡 알림 테스트 실패: " + error.message);
  }
}

async function disconnectKakao() {
  try {
    const response = await fetch("/auth/kakao/disconnect", { method: "POST" });
    if (!response.ok) throw new Error(response.statusText);
    await updateKakaoConnectionStatus();
  } catch (error) {
    alert("카카오 연결 해제 실패: " + error.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  renderHistoryButtons();
  updateKakaoConnectionStatus();

  const callbackParams = new URLSearchParams(window.location.search);
  if (callbackParams.get("kakao") === "connected") {
    window.history.replaceState({}, "", window.location.pathname);
  }

  const kakaoTestBtn = document.getElementById("kakao-test-btn");
  if (kakaoTestBtn) {
    kakaoTestBtn.addEventListener("click", testKakaoMessage);
  }

  const kakaoDisconnectBtn = document.getElementById("kakao-disconnect-btn");
  if (kakaoDisconnectBtn) {
    kakaoDisconnectBtn.addEventListener("click", disconnectKakao);
  }

  const testBtn = document.getElementById("testSiren");
  if (testBtn) {
    testBtn.addEventListener("click", () => {
      triggerAlarmPopup({
        title: "🛎 사이렌 테스트",
        messages: [
          "코레일에 표시된 결제기한 내에 결제해 주세요.",
          "충분히 들을 수 있게 볼륨을 조절해 주세요."
        ]
      });
    });
  }
});
