/*  OpenHomeBus Dashboard  Controller-Centric SPA  */

(() => {
  "use strict";

  /*  State  */
  let controllerData = {}; // ctrl_id  latest summary
  let devices = [];
  let currentDevice = null;
  let currentCtrl = null; // controller being viewed
  let ws = null;
  let lastStates = {}; // "ctrl/addr"  state

  /*  API  */
  const BASE = document.baseURI.replace(/\/$/, "");
  const u = (p) => `${BASE}/${p.replace(/^\//, "")}`;

  const API = {
    async get(p) {
      return (await fetch(u(p))).json();
    },
    async post(p, b) {
      return (
        await fetch(u(p), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(b),
        })
      ).json();
    },
    async put(p, b) {
      return (
        await fetch(u(p), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(b),
        })
      ).json();
    },
    async del(p) {
      return (await fetch(u(p), { method: "DELETE" })).json();
    },
  };

  /*  Helpers  */
  const $ = (s) => document.getElementById(s);
  const esc = (s) => {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  };
  const escA = (s) =>
    String(s)
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  const icons = () => {
    if (typeof lucide !== "undefined") lucide.createIcons();
  };
  const hex = (n) => "0x" + n.toString(16).toUpperCase().padStart(2, "0");

  function fmtUptime(s) {
    if (!s && s !== 0) return "";
    s = Math.floor(s);
    const d = Math.floor(s / 86400),
      h = Math.floor((s % 86400) / 3600),
      m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  function fmtNum(n) {
    return n != null ? Number(n).toLocaleString() : "";
  }

  function fmtPct(n) {
    return n != null ? n.toFixed(1) + "%" : "";
  }

  function wifiClass(q) {
    if (!q) return "";
    const l = q.toLowerCase();
    if (l === "excellent" || l === "good") return "wifi-good";
    if (l === "fair") return "wifi-fair";
    return "wifi-poor";
  }

  /*  Toast  */
  function toast(msg, type = "info") {
    const c = $("toasts"),
      el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(8px)";
      el.style.transition = "all .2s";
      setTimeout(() => el.remove(), 200);
    }, 3500);
  }

  /*  Navigation  */
  function showView(id) {
    document
      .querySelectorAll(".view")
      .forEach((v) => v.classList.remove("active"));
    document
      .querySelectorAll(".nav-btn")
      .forEach((b) => b.classList.remove("active"));
    const view = $(`view-${id}`);
    if (view) view.classList.add("active");
    const btn = document.querySelector(`.nav-btn[data-view="${id}"]`);
    if (btn) btn.classList.add("active");
  }

  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.dataset.view;
      showView(v);
      if (v === "controllers") loadControllers();
      if (v === "devices") loadDevices();
      if (v === "bus-log") loadBusLog();
      if (v === "settings") loadSettings();
    });
  });

  /*  WebSocket  */
  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}${u("/ws/bus").replace(location.origin, "")}`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      const b = $("ws-status");
      b.className = "status-badge online";
      b.innerHTML =
        '<i data-lucide="wifi" class="icon-sm"></i><span>Live</span>';
      icons();
    };

    ws.onclose = () => {
      const b = $("ws-status");
      b.className = "status-badge offline";
      b.innerHTML =
        '<i data-lucide="wifi-off" class="icon-sm"></i><span>Live</span>';
      icons();
      setTimeout(connectWs, 3000);
    };

    ws.onmessage = (e) => {
      try {
        handleWs(JSON.parse(e.data));
      } catch {}
    };
  }

  function handleWs(msg) {
    if (msg.type === "state") {
      lastStates[`${msg.controller}/${msg.address}`] = msg.state;
      if (
        currentDevice &&
        currentDevice.controller_id === msg.controller &&
        currentDevice.address === msg.address
      ) {
        renderIoState(msg.state);
      }
    } else if (msg.type === "event") {
      toast(
        `Bus: ${msg.event.event} addr=${hex(msg.event.address || 0)}`,
        "info",
      );
      loadDevices();
    } else if (msg.type === "diag") {
      // Real-time diagnostics update  refresh controllers view if active
      if ($("view-controllers").classList.contains("active")) loadControllers();
      if (
        $("view-controller-detail").classList.contains("active") &&
        currentCtrl
      )
        loadControllerDetail(currentCtrl);
    }
  }

  /*  Controllers (landing page)  */
  async function loadControllers() {
    try {
      const data = await API.get("/api/diagnostics/controllers");
      const ctrls = data.controllers || [];
      const bus = data.bus_stats || {};
      controllerData = {};
      ctrls.forEach((c) => (controllerData[c.controller_id] = c));

      // Aggregate stats
      const online = ctrls.filter((c) => c.online).length;
      $("stat-total-controllers").textContent = ctrls.length;
      $("stat-online-controllers").textContent = `${online} online`;
      $("stat-total-nodes").textContent = fmtNum(
        bus.active_nodes ||
          ctrls.reduce((s, c) => s + (c.active_nodes || 0), 0),
      );
      $("stat-total-frames").textContent = fmtNum(
        bus.total_frames ||
          ctrls.reduce((s, c) => s + (c.total_frames || 0), 0),
      );
      $("stat-error-rate").textContent = fmtPct(
        bus.error_rate != null ? bus.error_rate : 0,
      );

      renderControllerGrid(ctrls);
    } catch (e) {
      toast("Failed to load controllers", "error");
    }
  }

  function renderControllerGrid(ctrls) {
    const grid = $("controller-grid");
    grid.innerHTML = "";

    if (ctrls.length === 0) {
      grid.innerHTML = `<div class="empty-state"><i data-lucide="radio" style="width:48px;height:48px;opacity:0.3"></i><p>No controllers discovered yet</p><p class="text-muted">Controllers will appear here once they connect via MQTT</p></div>`;
      icons();
      return;
    }

    ctrls.forEach((c) => {
      const card = document.createElement("div");
      card.className = "controller-card";
      const online = c.online;
      const statusCls = online ? "online" : "offline";
      const statusTxt = online ? "Online" : "Offline";
      const wifi = c.wifi_quality || "";
      const wCls = wifiClass(c.wifi_quality);

      card.innerHTML = `
        <div class="ctrl-card-header">
          <span class="ctrl-card-id">${esc(c.controller_id)}</span>
          <span class="ctrl-status ${statusCls}">${statusTxt}</span>
        </div>
        ${
          online
            ? `<div class="ctrl-card-stats">
          <div class="ctrl-stat"><span class="ctrl-stat-label">Uptime</span><span class="ctrl-stat-value">${fmtUptime(c.uptime_s)}</span></div>
          <div class="ctrl-stat"><span class="ctrl-stat-label">WiFi</span><span class="ctrl-stat-value ${wCls}">${esc(String(wifi))}</span></div>
          <div class="ctrl-stat"><span class="ctrl-stat-label">Nodes</span><span class="ctrl-stat-value">${c.active_nodes ?? ""}</span></div>
          <div class="ctrl-stat"><span class="ctrl-stat-label">Firmware</span><span class="ctrl-stat-value">${esc(c.fw_version || "")}</span></div>
        </div>`
            : `<p class="text-muted" style="margin-top:8px">Controller is offline</p>`
        }`;

      card.addEventListener("click", () =>
        openControllerDetail(c.controller_id),
      );
      grid.appendChild(card);
    });
    icons();
  }

  /*  Controller Detail  */
  async function openControllerDetail(ctrlId) {
    currentCtrl = ctrlId;
    $("detail-ctrl-title").textContent = ctrlId;
    showView("controller-detail");
    await loadControllerDetail(ctrlId);
  }

  async function loadControllerDetail(ctrlId) {
    try {
      const data = await API.get(
        `/api/diagnostics/controllers/${encodeURIComponent(ctrlId)}`,
      );
      const c = data.controller || {};
      const nodes = data.nodes || [];

      // System info
      const online = c.online;
      $("detail-status").innerHTML = online
        ? '<span class="ctrl-status online">Online</span>'
        : '<span class="ctrl-status offline">Offline</span>';
      $("detail-uptime").textContent = fmtUptime(c.uptime_s);
      $("detail-fw").textContent = c.fw_version || "";
      const wCls = wifiClass(c.wifi_quality);
      $("detail-wifi").innerHTML =
        c.wifi_rssi != null
          ? `<span class="${wCls}">${c.wifi_rssi} dBm (${esc(c.wifi_quality || "")})</span>`
          : "";
      const heapPct = c.heap_usage_pct;
      $("detail-heap").textContent =
        heapPct != null ? heapPct.toFixed(1) + "%" : "";
      $("detail-free-heap").textContent =
        c.free_heap != null ? (c.free_heap / 1024).toFixed(0) + " KB" : "";

      // Bus stats
      $("detail-nodes").textContent = c.active_nodes ?? "";
      $("detail-voltage").textContent =
        c.bus_voltage != null ? c.bus_voltage + " V" : "";
      $("detail-util").textContent =
        c.bus_utilization != null ? c.bus_utilization + "%" : "";
      $("detail-token").textContent =
        c.token_cycle_ms != null ? c.token_cycle_ms + " ms" : "";
      $("detail-rxtx").textContent =
        `${fmtNum(c.rx_frames)} / ${fmtNum(c.tx_frames)}`;
      $("detail-crc").textContent = fmtNum(c.crc_errors);
      $("detail-err-rate").textContent = fmtPct(c.error_rate);

      // Nodes table
      const tbody = $("detail-nodes-table").querySelector("tbody");
      tbody.innerHTML = "";
      if (nodes.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px">No nodes on this controller</td></tr>';
        return;
      }
      nodes.forEach((n) => {
        const tr = document.createElement("tr");
        const st = n.online
          ? '<span class="ctrl-status online" style="font-size:11px">Online</span>'
          : '<span class="ctrl-status offline" style="font-size:11px">Offline</span>';
        tr.innerHTML = `
          <td class="mono">${hex(n.address)}</td>
          <td class="mono">${esc(n.hw_id || "")}</td>
          <td>${st}</td>
          <td>${fmtNum(n.rx_frames)}</td>
          <td>${fmtNum(n.tx_frames)}</td>
          <td>${fmtNum(n.crc_errors)}</td>
          <td>${fmtPct(n.error_rate)}</td>`;
        tbody.appendChild(tr);
      });
    } catch (e) {
      toast("Failed to load controller detail", "error");
    }
  }

  $("btn-back-controllers").addEventListener("click", () => {
    currentCtrl = null;
    showView("controllers");
    loadControllers();
  });

  $("btn-remove-controller").addEventListener("click", async () => {
    if (!currentCtrl) return;
    if (!confirm(`Remove controller "${currentCtrl}" and all its HA entities?`))
      return;
    try {
      const r = await API.del(
        `/api/controllers/${encodeURIComponent(currentCtrl)}`,
      );
      if (r.ok) {
        const parts = [`${r.entities_removed} HA entities cleared`];
        if (r.mqtt_cleared) parts.push("MQTT disconnected");
        toast(`Removed controller (${parts.join(", ")})`, "success");
        currentCtrl = null;
        showView("controllers");
        await loadControllers();
      } else {
        toast(r.error || "Failed", "error");
      }
    } catch {
      toast("Failed to remove controller", "error");
    }
  });

  $("btn-refresh-controllers").addEventListener("click", loadControllers);

  /*  Devices  */
  async function loadDevices() {
    try {
      const data = await API.get("/api/devices");
      devices = data.devices || [];
      renderDeviceGrid();
    } catch {
      toast("Failed to load devices", "error");
    }
  }

  function renderDeviceGrid() {
    const grid = $("device-grid");
    grid.innerHTML = "";
    if (devices.length === 0) {
      grid.innerHTML = `<div class="empty-state"><i data-lucide="cpu" style="width:48px;height:48px;opacity:0.3"></i><p>No devices registered</p><p class="text-muted">Devices appear when nodes join the bus</p></div>`;
      icons();
      return;
    }
    devices.forEach((dev) => {
      const card = document.createElement("div");
      card.className = "device-card";
      const online = dev.state === "online";
      card.innerHTML = `
        <div class="dev-card-name">${esc(dev.name)}</div>
        <div class="dev-card-info">${hex(dev.address)}  ${esc(dev.controller_id)} ${dev.type_label ? " " + esc(dev.type_label) : ""}</div>
        <span class="dev-card-state ${online ? "online" : "offline"}">${online ? "Online" : "Offline"}</span>`;
      card.addEventListener("click", () => openDeviceDetail(dev));
      grid.appendChild(card);
    });
    icons();
  }

  /*  Device Detail  */
  function openDeviceDetail(dev) {
    currentDevice = dev;
    $("dev-detail-title").textContent = dev.name;
    $("dev-name").value = dev.name;
    $("dev-room").value = dev.room || "";
    $("dev-hw-id").textContent = dev.hw_id || "";
    $("dev-type").textContent = dev.type_label || "";
    $("dev-ctrl").textContent = dev.controller_id;
    $("dev-addr").textContent = hex(dev.address);
    $("dev-fw").textContent = dev.fw_version || "";
    $("dev-state").innerHTML =
      dev.state === "online"
        ? '<span class="ctrl-status online">Online</span>'
        : '<span class="ctrl-status offline">Offline</span>';

    // DI config
    const diList = $("dev-di-list");
    diList.innerHTML = "";
    for (let i = 0; i < dev.di_count; i++) {
      diList.appendChild(
        ioConfigRow(
          "di",
          i,
          (dev.di_names || [])[i] || `Input ${i + 1}`,
          (dev.di_classes || [])[i] || "",
          "Device Class",
        ),
      );
    }
    // DO config
    const doList = $("dev-do-list");
    doList.innerHTML = "";
    for (let i = 0; i < dev.do_count; i++) {
      doList.appendChild(
        ioConfigRow(
          "do",
          i,
          (dev.do_names || [])[i] || `Output ${i + 1}`,
          (dev.do_types || [])[i] || "switch",
          "HA Type",
        ),
      );
    }

    // Live I/O
    const key = `${dev.controller_id}/${dev.address}`;
    if (lastStates[key]) renderIoState(lastStates[key]);
    else
      $("dev-io-grid").innerHTML =
        '<p class="text-muted">Waiting for state</p>';

    showView("device-detail");
    icons();
  }

  function ioConfigRow(prefix, idx, name, extra, placeholder) {
    const row = document.createElement("div");
    row.className = "io-config-row";
    row.innerHTML = `
      <span class="io-ch">${prefix.toUpperCase()} ${idx}</span>
      <input class="input io-name" data-prefix="${prefix}" data-idx="${idx}" value="${escA(name)}" placeholder="Name" />
      <input class="input io-extra" data-prefix="${prefix}" data-idx="${idx}" value="${escA(extra)}" placeholder="${escA(placeholder)}" />`;
    return row;
  }

  function renderIoState(state) {
    const grid = $("dev-io-grid");
    grid.innerHTML = "";
    if (state.di)
      state.di.forEach((v, i) => {
        const cell = document.createElement("div");
        cell.className = `io-cell ${v ? "on" : "off"}`;
        cell.innerHTML = `<span class="io-label">DI${i}</span><span class="io-val">${v ? "ON" : "OFF"}</span>`;
        grid.appendChild(cell);
      });
    if (state.do)
      state.do.forEach((v, i) => {
        const cell = document.createElement("div");
        cell.className = `io-cell ${v ? "on" : "off"}`;
        cell.innerHTML = `<span class="io-label">DO${i}</span><span class="io-val">${v ? "ON" : "OFF"}</span>`;
        grid.appendChild(cell);
      });
  }

  $("btn-back-devices").addEventListener("click", () => {
    currentDevice = null;
    showView("devices");
    loadDevices();
  });

  /*  Device Save  */
  $("btn-dev-save").addEventListener("click", async () => {
    if (!currentDevice) return;
    const ctrl = currentDevice.controller_id;
    const addr = currentDevice.address
      .toString(16)
      .toUpperCase()
      .padStart(2, "0");
    const diNames = [],
      diClasses = [],
      doNames = [],
      doTypes = [];
    $("dev-di-list")
      .querySelectorAll(".io-name")
      .forEach((el) => diNames.push(el.value));
    $("dev-di-list")
      .querySelectorAll(".io-extra")
      .forEach((el) => diClasses.push(el.value));
    $("dev-do-list")
      .querySelectorAll(".io-name")
      .forEach((el) => doNames.push(el.value));
    $("dev-do-list")
      .querySelectorAll(".io-extra")
      .forEach((el) => doTypes.push(el.value));
    try {
      const r = await API.put(`/api/devices/${ctrl}/${addr}`, {
        name: $("dev-name").value,
        room: $("dev-room").value,
        di_names: diNames,
        do_names: doNames,
        di_classes: diClasses,
        do_types: doTypes,
      });
      if (r.error) toast(r.error, "error");
      else {
        toast("Device saved", "success");
        currentDevice = r;
      }
    } catch {
      toast("Failed to save", "error");
    }
  });

  /*  HA Discover / Remove  */
  $("btn-dev-ha-publish").addEventListener("click", async () => {
    if (!currentDevice) return;
    const ctrl = currentDevice.controller_id,
      addr = currentDevice.address.toString(16).toUpperCase().padStart(2, "0");
    try {
      const r = await API.post(`/api/devices/${ctrl}/${addr}/ha-discover`, {});
      if (r.error) toast(r.error, "error");
      else toast(`Published ${r.entities_published} entities`, "success");
    } catch {
      toast("Failed to publish", "error");
    }
  });

  $("btn-dev-ha-remove").addEventListener("click", async () => {
    if (!currentDevice) return;
    const ctrl = currentDevice.controller_id,
      addr = currentDevice.address.toString(16).toUpperCase().padStart(2, "0");
    try {
      const r = await API.del(`/api/devices/${ctrl}/${addr}/ha-discover`);
      if (r.error) toast(r.error, "error");
      else toast(`Removed ${r.entities_removed} entities`, "success");
    } catch {
      toast("Failed to remove", "error");
    }
  });

  /*  Delete Device  */
  $("btn-dev-delete").addEventListener("click", () => {
    if (!currentDevice) return;
    $("modal-delete").removeAttribute("hidden");
  });

  $("btn-modal-cancel").addEventListener("click", () =>
    $("modal-delete").setAttribute("hidden", ""),
  );

  $("btn-modal-confirm").addEventListener("click", async () => {
    if (!currentDevice) return;
    const ctrl = currentDevice.controller_id,
      addr = currentDevice.address.toString(16).toUpperCase().padStart(2, "0");
    try {
      await API.del(`/api/devices/${ctrl}/${addr}`);
      toast(`Deleted "${currentDevice.name}"`, "success");
      $("modal-delete").setAttribute("hidden", "");
      currentDevice = null;
      showView("devices");
      await loadDevices();
    } catch {
      toast("Failed to delete", "error");
    }
  });

  /*  Bus Log  */
  async function loadBusLog() {
    try {
      const data = await API.get("/api/diagnostics/events?limit=100");
      renderBusLog(data.events || []);
    } catch {
      toast("Failed to load log", "error");
    }
  }

  function renderBusLog(events) {
    const tbody = $("bus-log-table").querySelector("tbody");
    tbody.innerHTML = "";
    if (events.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:32px">No events recorded</td></tr>';
      return;
    }
    events.forEach((ev) => {
      const tr = document.createElement("tr");
      const ts = ev.timestamp
        ? new Date(ev.timestamp * 1000).toLocaleString()
        : ev.ago || "";
      tr.innerHTML = `
        <td class="mono">${esc(String(ts))}</td>
        <td>${esc(ev.controller_id || "")}</td>
        <td class="mono">${ev.address != null ? hex(ev.address) : ""}</td>
        <td>${esc(ev.event_type || ev.event || "")}</td>
        <td class="mono">${ev.data ? esc(JSON.stringify(ev.data)) : ev.message ? esc(ev.message) : ""}</td>`;
      tbody.appendChild(tr);
    });
  }

  $("btn-refresh-log").addEventListener("click", loadBusLog);

  /*  MQTT Status  */
  async function checkMqtt() {
    try {
      const data = await API.get("/api/mqtt/status");
      const b = $("mqtt-status");
      b.className = data.connected
        ? "status-badge online"
        : "status-badge offline";
      b.innerHTML = data.connected
        ? '<i data-lucide="cloud" class="icon-sm"></i><span>MQTT</span>'
        : '<i data-lucide="cloud-off" class="icon-sm"></i><span>MQTT</span>';
      icons();
    } catch {}
  }

  /*  Settings  */
  async function loadSettings() {
    try {
      const data = await API.get("/api/settings");
      $("set-broker").value = data.mqtt_broker || "";
      $("set-port").value = data.mqtt_port || 1883;
      $("set-user").value = data.mqtt_username || "";
      $("set-pass").value = data.mqtt_password_set ? "********" : "";
    } catch {
      toast("Failed to load settings", "error");
    }
  }

  $("btn-save-settings").addEventListener("click", async () => {
    const body = {
      mqtt_broker: $("set-broker").value.trim(),
      mqtt_port: parseInt($("set-port").value) || 1883,
      mqtt_username: $("set-user").value.trim(),
      mqtt_password: $("set-pass").value,
    };
    try {
      const r = await API.post("/api/settings", body);
      if (r.ok) {
        toast("Settings saved  reconnecting MQTT", "success");
        setTimeout(checkMqtt, 3000);
      } else toast(r.error || "Save failed", "error");
    } catch {
      toast("Failed to save settings", "error");
    }
  });

  /*  Controller Discovery (in settings)  */
  $("btn-scan-controllers").addEventListener("click", () => {
    const res = $("scan-results");
    const btn = $("btn-scan-controllers");
    btn.disabled = true;
    res.innerHTML = `
      <div class="scan-progress">
        <div class="scan-progress-bar" id="scan-bar" style="width:0%"></div>
      </div>
      <p class="text-muted scan-stage" id="scan-stage">Starting scan…</p>`;
    const list = document.createElement("div");
    list.className = "scan-list";
    res.appendChild(list);

    const es = new EventSource(u("/api/controllers/discover"));
    const found = [];

    es.addEventListener("progress", (e) => {
      const d = JSON.parse(e.data);
      const bar = $("scan-bar");
      const stage = $("scan-stage");
      if (bar) bar.style.width = d.pct + "%";
      if (stage) stage.textContent = d.stage;
    });

    es.addEventListener("controller", (e) => {
      const ctrl = JSON.parse(e.data);
      found.push(ctrl);
      const card = document.createElement("div");
      card.className = "scan-card";
      const provisioned = ctrl.already_provisioned;
      card.innerHTML = `
        <div class="scan-card-info">
          <span class="scan-card-id">${esc(ctrl.controller_id || ctrl.name || "Unknown")}</span>
          <span class="scan-card-ip">${esc(ctrl.ip)}</span>
        </div>
        ${
          provisioned
            ? `<span class="badge badge-connected"><i data-lucide="check-circle" class="icon-sm"></i> Connected</span>
               <button class="btn btn-outline btn-sm btn-prov" data-ip="${escA(ctrl.ip)}"><i data-lucide="refresh-cw" class="icon-sm"></i> Re-provision</button>`
            : `<button class="btn btn-primary btn-sm btn-prov" data-ip="${escA(ctrl.ip)}"><i data-lucide="send" class="icon-sm"></i> Provision</button>`
        }`;
      if (provisioned) card.classList.add("scan-card--connected");
      list.appendChild(card);
      // Bind provision button
      const provBtn = card.querySelector(".btn-prov");
      if (provBtn) {
        provBtn.addEventListener("click", async () => {
          provBtn.disabled = true;
          provBtn.textContent = "…";
          try {
            const r = await API.post("/api/controllers/provision", {
              ip: provBtn.dataset.ip,
            });
            if (r.ok) toast("Controller provisioned", "success");
            else toast(r.error || "Failed", "error");
          } catch {
            toast("Provision failed", "error");
          }
          provBtn.disabled = false;
          provBtn.innerHTML =
            '<i data-lucide="send" class="icon-sm"></i> Provision';
          icons();
        });
      }
      icons();
    });

    es.addEventListener("done", (e) => {
      es.close();
      btn.disabled = false;
      const bar = $("scan-bar");
      const stage = $("scan-stage");
      if (bar) bar.style.width = "100%";
      const d = JSON.parse(e.data);
      if (stage)
        stage.textContent = `Scan complete — ${d.total} controller${d.total !== 1 ? "s" : ""} found`;
      if (found.length === 0) {
        list.innerHTML =
          '<p class="text-muted">No controllers found on network</p>';
      }
    });

    es.onerror = () => {
      es.close();
      btn.disabled = false;
      const stage = $("scan-stage");
      if (stage) stage.textContent = "Scan complete";
      if (found.length === 0) {
        list.innerHTML =
          '<p class="text-muted">No controllers found on network</p>';
      }
    };
  });

  $("btn-manual-add").addEventListener("click", async () => {
    const ip = $("manual-ip").value.trim();
    if (!ip) {
      toast("Enter an IP address", "error");
      return;
    }
    try {
      const info = await API.get(`/api/controllers/${ip}/info`);
      if (info.error) {
        toast(info.error, "error");
        return;
      }
      toast(`Found controller: ${info.controller_id}`, "success");
      await API.post("/api/controllers/provision", { ip });
      $("manual-ip").value = "";
    } catch {
      toast(`Cannot reach ${ip}`, "error");
    }
  });

  /*  Version  */
  async function loadInfo() {
    try {
      const data = await API.get("/api/info");
      $("version-badge").textContent = `v${data.version}`;
    } catch {}
  }

  /*  Init  */
  icons();
  loadControllers();
  loadInfo();
  checkMqtt();
  connectWs();
  setInterval(checkMqtt, 15000);
  setInterval(() => {
    if ($("view-controllers").classList.contains("active")) loadControllers();
  }, 10000);
})();
