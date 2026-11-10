const $ = (id) => document.getElementById(id);
let busy = false;
let coursesLoadedFor = "";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `请求失败：HTTP ${response.status}`);
  return body;
}

function notify(message, isError = false) {
  const node = $("notice");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.remove("hidden");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => node.classList.add("hidden"), 5000);
}

function statusClass(value) {
  return /失败|异常|不可|未配置/.test(value || "") ? "status bad" : "status";
}

function renderEvents(events) {
  $("eventCount").textContent = `${events.length} 条`;
  if (!events.length) {
    $("eventsBody").innerHTML = '<tr class="empty-row"><td colspan="7">启动监听后，新的签到会显示在这里</td></tr>';
    $("eventCards").innerHTML = "";
    return;
  }
  $("eventsBody").innerHTML = events.map((event) => `<tr>
    <td>${escapeHtml(event.detected_time || "-")}</td>
    <td><strong>${escapeHtml(event.course_title)}</strong><small>${escapeHtml(event.teacher || "")}</small></td>
    <td>${escapeHtml(event.rollcall_type)}</td><td>${escapeHtml(event.attendance || "-")}</td>
    <td class="code">${escapeHtml(event.number_code || "-")}</td>
    <td><span class="${statusClass(event.strategy_status)}">${escapeHtml(event.strategy_status)}</span></td>
    <td><span class="${statusClass(event.result)}">${escapeHtml(event.result)}</span></td></tr>`).join("");
  $("eventCards").innerHTML = events.map((event) => `<article class="event-card"><header><h3>${escapeHtml(event.course_title)}</h3><span class="${statusClass(event.result)}">${escapeHtml(event.result)}</span></header><dl><div><dt>类型</dt><dd>${escapeHtml(event.rollcall_type)}</dd></div><div><dt>到课</dt><dd>${escapeHtml(event.attendance || "-")}</dd></div><div><dt>签到码</dt><dd class="code">${escapeHtml(event.number_code || "-")}</dd></div><div><dt>策略</dt><dd>${escapeHtml(event.strategy_status)}</dd></div></dl></article>`).join("");
}

function render(state) {
  $("version").textContent = state.version;
  $("loginPill").textContent = state.logged_in ? "已登录" : "未登录";
  $("loginPill").className = `pill ${state.logged_in ? "live" : "muted"}`;
  $("monitorPill").textContent = state.monitoring ? "监听中" : "未监听";
  $("monitorPill").className = `pill ${state.monitoring ? "live" : "muted"}`;
  $("accountMetric").textContent = state.account ? `${state.account.name || state.account.username}` : "-";
  $("queryMetric").textContent = state.query_count;
  $("checkMetric").textContent = state.last_check ? state.last_check.slice(11) : "-";
  $("autoMetric").textContent = state.auto_submit ? "开启" : "关闭";
  $("threshold").value = state.threshold;
  $("thresholdValue").value = `${state.threshold}%`;
  $("autoSubmit").checked = state.auto_submit;
  renderEvents(state.events || []);
  $("locations").innerHTML = (state.locations || []).map((location) => `<div class="location"><div><strong>${escapeHtml(location.name)}</strong><span>${location.latitude}, ${location.longitude} · ±${location.accuracy}m</span></div>${location.built_in ? "<em>系统内置</em>" : ""}</div>`).join("");
  $("locationSelect").innerHTML = '<option value="">请选择地点</option>' + (state.locations || []).map((location) => `<option value="${escapeHtml(location.id)}">${escapeHtml(location.name)}</option>`).join("");
  const bindingCount = Object.keys(state.bindings || {}).length;
  $("bindingSummary").textContent = `当前账号已绑定 ${bindingCount} 门课程；地点绑定继续与桌面版共用。`;
  $("logs").innerHTML = (state.logs || []).slice(0, 20).map((log) => `<li><time>${escapeHtml(log.time)}</time><span class="${escapeHtml(log.level)}">${escapeHtml(log.message)}</span></li>`).join("");
  if (state.last_error) notify(state.last_error, true);
  const accountKey = state.account?.username || "";
  if (state.logged_in && coursesLoadedFor !== accountKey) loadCourses(accountKey);
  if (!state.logged_in) { coursesLoadedFor = ""; $("courseSelect").innerHTML = '<option value="">登录后读取课程</option>'; }
}

async function loadCourses(accountKey) {
  coursesLoadedFor = "__loading__";
  try {
    const body = await api("/api/courses");
    $("courseSelect").innerHTML = '<option value="">请选择课程</option>' + (body.courses || []).map((course) => `<option value="${escapeHtml(course.course_id)}" data-title="${escapeHtml(course.title)}">${escapeHtml(course.title)}${course.term ? ` · ${escapeHtml(course.term)}` : ""}</option>`).join("");
    coursesLoadedFor = accountKey;
  } catch (error) { coursesLoadedFor = ""; notify(`课程列表读取失败：${error.message}`, true); }
}

async function action(fn, success) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("button").forEach((button) => button.disabled = true);
  try { render(await fn()); if (success) notify(success); }
  catch (error) { notify(error.message, true); }
  finally { busy = false; document.querySelectorAll("button").forEach((button) => button.disabled = false); }
}

$("threshold").addEventListener("input", (event) => $("thresholdValue").value = `${event.target.value}%`);
$("loginForm").addEventListener("submit", (event) => { event.preventDefault(); action(() => api("/api/login", {method:"POST", body:JSON.stringify({username:$("username").value.trim(), password:$("password").value})}), "登录成功").finally(() => $("password").value = ""); });
$("restoreBtn").addEventListener("click", () => action(() => api("/api/session/restore", {method:"POST"}), "会话恢复成功"));
$("disconnectBtn").addEventListener("click", () => action(() => api("/api/session/disconnect", {method:"POST"}), "Web 会话已断开"));
$("monitorForm").addEventListener("submit", (event) => { event.preventDefault(); action(async () => { await api("/api/settings", {method:"PUT", body:JSON.stringify({threshold:Number($("threshold").value), auto_submit:$("autoSubmit").checked})}); return api("/api/monitor/start", {method:"POST", body:JSON.stringify({auto_submit:$("autoSubmit").checked})}); }, "监听已启动"); });
$("stopBtn").addEventListener("click", () => action(() => api("/api/monitor/stop", {method:"POST"}), "监听已停止"));
$("bindingForm").addEventListener("submit", (event) => { event.preventDefault(); const option = $("courseSelect").selectedOptions[0]; action(() => api("/api/location-binding", {method:"PUT", body:JSON.stringify({course_id:$("courseSelect").value, course_title:option?.dataset.title || option?.textContent || "", preset_id:$("locationSelect").value})}), "课程地点绑定已保存"); });

async function refresh() { if (busy) return; try { render(await api("/api/status")); } catch (error) { notify(error.message, true); } }
refresh();
setInterval(refresh, 2500);
