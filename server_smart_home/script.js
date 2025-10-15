/*
==========================================================
=          Architect, developer and researcher:          =
=                     Matanel Zarfati                    =
==========================================================
*/


(() => {
  const JSON_PATH = "devices.json";
  const POLL_MS   = 1000;

  let devicesState = {};

  // DOM references
  const insideGridEl  = document.getElementById("inside-grid");
  const outsideGridEl = document.getElementById("outside-grid");
  const tagLights     = document.getElementById("tag-lights");
  const tagWindows    = document.getElementById("tag-windows");
  const tagDoors      = document.getElementById("tag-doors");

  /* ───── SVG ICON FACTORIES ───── */
  function createLightSVG() {
    const ns  = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.innerHTML = `
      <!-- round bulb -->
      <circle fill="currentColor" cx="12" cy="8" r="6"/>

      <!-- neck -->
      <rect   fill="currentColor" x="9"  y="13" width="6" height="3" rx="1"/>

      <!-- base line: narrower and pulled up -->
      <rect   fill="currentColor" x="9"  y="17" width="6" height="2" rx="1"/>
    `;
    return svg;
  }

  function createWindowSVG() {
    const ns  = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.innerHTML = `
      <rect fill="none" stroke="currentColor" stroke-width="2" x="3"  y="3"  width="8"  height="8"/>
      <rect fill="none" stroke="currentColor" stroke-width="2" x="13" y="3"  width="8"  height="8"/>
      <rect fill="none" stroke="currentColor" stroke-width="2" x="3"  y="13" width="8"  height="8"/>
      <rect fill="none" stroke="currentColor" stroke-width="2" x="13" y="13" width="8"  height="8"/>
    `;
    return svg;
  }

  function createDoorSVG() {
    const ns  = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.innerHTML = `
      <path  fill="currentColor" d="M12 2l8 4v14H4V6l8-4zm0 
         2.18L6 6v12h12V6l-6-1.82z"/>
      <circle fill="currentColor" cx="15" cy="12" r="1"/>
    `;
    return svg;
  }

  function createFanSVG() {
    const ns  = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.innerHTML = `
      <path fill="currentColor" d="M19.14 12.94a7.5 7.5 0 000-1.88l2.03
         -1.58a.5.5 0 00.12-.64l-1.92-3.32a.5.5 
         0 00-.61-.22l-2.39.96a7.52 7.52 0 
         00-1.6-.93L14.65 2.5a.5.5 0 00-.5-.5h
         -4.3a.5.5 0 00-.5.5l-.38 2.72a7.52 
         7.52 0 00-1.6.93l-2.39-.96a.5.5 0 
         00-.61.22L2.7 8.84a.5.5 0 00.12.64
         l2.03 1.58a7.5 7.5 0 000 1.88L2.82 
         14.08a.5.5 0 00-.12.64l1.92 
         3.32c.15.26.45.35.7.22l2.39-.96c.5 
         .36 1.04.68 1.6.93l.38 2.72c.05
         .28.28.5.57.5h4.3c.29 0 .52-.22
         .57-.5l.38-2.72c.56-.25
         1.1-.57 1.6-.93l2.39.96c.25.1
         .55.04.7-.22l1.92-3.32a.5.5 
         0 00-.12-.64l-2.03-1.58zM12 15.5
         A3.5 3.5 0 1115.5 12 3.5 3.5 0 
         0112 15.5z"/>
    `;
    return svg;
  }

  /* ───── BUILD A DEVICE CARD ───── */
  function makeDeviceCard(device) {
    const card = document.createElement("div");
    card.classList.add("device-card", device.mode === 1 ? "on" : "off");
    card.id = `device-${device.number}`;

    const iconContainer = document.createElement("div");
    iconContainer.classList.add("icon-container");

    if (device.name.toLowerCase() === "windows") {
      iconContainer.appendChild(createWindowSVG());
    } else {
      switch ((device.type || "light").toLowerCase()) {
        case "light":
          iconContainer.appendChild(createLightSVG());
          break;
        case "door":
          iconContainer.appendChild(createDoorSVG());
          break;
        case "fan":
          iconContainer.appendChild(createFanSVG());
          break;
        default:
          iconContainer.appendChild(createLightSVG());
      }
    }

    const nameEl = document.createElement("div");
    nameEl.className = "name";
    nameEl.textContent = device.name;

    const statEl = document.createElement("div");
    statEl.className = "status-text";
    statEl.textContent = device.mode === 1 ? "On" : "Off";

    card.append(iconContainer, nameEl, statEl);
    return card;
  }

  /* ───── INITIAL RENDER & POLLING LOOP ───── */
  function renderInitial(devices) {
    insideGridEl.innerHTML  = "";
    outsideGridEl.innerHTML = "";

    const insideOrder  = ["Room Light", "Bed Light", "Bathroom Light", "Windows"];
    const outsideOrder = ["Garden Light", "Garage Door"];

    insideOrder.forEach(name => {
      const d = devices.find(x => x.name === name);
      if (d) {
        insideGridEl.appendChild(makeDeviceCard(d));
        devicesState[d.number] = { ...d };
      }
    });

    outsideOrder.forEach(name => {
      const d = devices.find(x => x.name === name);
      if (d) {
        outsideGridEl.appendChild(makeDeviceCard(d));
        devicesState[d.number] = { ...d };
      }
    });
  }

  function updateDevices(devices) {
    devices.forEach(d => {
      const prev = devicesState[d.number];
      if (!prev || prev.mode === d.mode) return;
      const card = document.getElementById(`device-${d.number}`);
      card.classList.replace(
        prev.mode === 1 ? "on" : "off",
        d.mode   === 1 ? "on" : "off"
      );
      card.querySelector(".status-text").textContent = d.mode === 1 ? "On" : "Off";
      devicesState[d.number].mode = d.mode;
    });
  }

  function updateTags(devices) {
    const lightsOn  = devices.some(d => d.type === "light"   && d.mode === 1);
    const windowsOn = devices.some(d => d.name === "Windows" && d.mode === 1);
    const doorsOn   = devices.some(d => d.type === "door"    && d.mode === 1);

    tagLights .classList.toggle("active", lightsOn);
    tagWindows.classList.toggle("active", windowsOn);
    tagDoors  .classList.toggle("active", doorsOn);
  }

  let firstLoad = true;
  function fetchAndUpdate() {
    fetch(JSON_PATH, { cache: "no-store" })
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(arr => {
        if (firstLoad) {
          renderInitial(arr);
          firstLoad = false;
        } else {
          updateDevices(arr);
        }
        updateTags(arr);
      })
      .catch(console.error);
  }

  document.addEventListener("DOMContentLoaded", () => {
    fetchAndUpdate();
    setInterval(fetchAndUpdate, POLL_MS);
  });
})();
