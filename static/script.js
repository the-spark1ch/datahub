let currentPath = "";
let searchQuery = "";
let searchTimer = null;

// Инициализация при загрузке
document.addEventListener("DOMContentLoaded", () => {
  const searchInput = document.getElementById("searchInput");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        searchQuery = (searchInput.value || "").trim();
        load();
      }, 250);
    });

    // Esc — быстро очистить поиск
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        searchInput.value = "";
        searchQuery = "";
        load();
      }
    });
  }

  load();
  setupDragAndDrop(); // <--- Подключаем Drag & Drop
});

function load() {
  if (searchQuery) {
    renderSearchBreadcrumbs(searchQuery);
    search(searchQuery);
    return;
  }
  renderBreadcrumbs(currentPath);
  fetch(`/api/list?path=${encodeURIComponent(currentPath)}`)
    .then((r) => r.json())
    .then((items) => renderItems(items, { mode: "browse" }));
}

function renderItems(items, { mode }) {
  const list = document.getElementById("fileList");
  list.innerHTML = "";

  if (mode === "browse" && currentPath) {
    const back = document.createElement("li");
    back.textContent = "⬅ Назад";
    back.onclick = () => {
      currentPath = currentPath.split("/").slice(0, -1).join("/");
      load();
    };
    list.appendChild(back);
  }

  items.forEach((item) => {
    const li = document.createElement("li");

    const nameSpan = document.createElement("span");
    nameSpan.className = "item-name";
    nameSpan.textContent = `${item.is_dir ? "📁" : "📄"} ${item.name}`;

    // В режиме поиска показываем путь (чтобы было понятно где найдено)
    if (mode === "search") {
      const sub = document.createElement("span");
      sub.className = "subpath";
      sub.textContent = `(${item.path})`;
      nameSpan.appendChild(sub);
    }

    // Double-click rename (только по названию)
    nameSpan.ondblclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      renameItem(item.path, item.name);
    };

    const actions = document.createElement("div");
    actions.className = "file-actions";

    if (item.is_dir) {
      const delBtn = document.createElement("button");
      delBtn.className = "delete";
      delBtn.textContent = "Удалить";
      delBtn.onclick = (e) => del(e, item.path);
      actions.appendChild(delBtn);

      li.appendChild(nameSpan);
      li.appendChild(actions);

      li.onclick = () => {
        // В поиске переход в найденную папку тоже должен работать
        currentPath = item.path;
        // сбрасываем поиск при навигации
        const si = document.getElementById("searchInput");
        if (si) si.value = "";
        searchQuery = "";
        load();
      };
    } else {
      const dlBtn = document.createElement("button");
      dlBtn.textContent = "Скачать";
      dlBtn.onclick = (e) => {
        e.stopPropagation();
        download(item.path);
      };

      const delBtn = document.createElement("button");
      delBtn.className = "delete";
      delBtn.textContent = "Удалить";
      delBtn.onclick = (e) => del(e, item.path);

      actions.appendChild(dlBtn);
      actions.appendChild(delBtn);

      li.appendChild(nameSpan);
      li.appendChild(actions);
    }

    list.appendChild(li);
  });
}

function search(q) {
  fetch(`/api/search?q=${encodeURIComponent(q)}`)
    .then((r) => r.json())
    .then((items) => renderItems(items, { mode: "search" }));
}

function renameItem(path, oldName) {
  const newName = prompt("Новое имя", oldName);
  if (!newName) return;
  const trimmed = newName.trim();
  if (!trimmed || trimmed === oldName) return;

  fetch("/api/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, name: trimmed }),
  })
    .then(async (r) => {
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        const code = data && data.error ? data.error : "rename_failed";
        if (code === "already_exists") alert("Файл/папка с таким именем уже существует.");
        else if (code === "bad_name") alert("Некорректное имя.");
        else if (code === "not_found") alert("Файл/папка не найден(а).");
        else alert("Не удалось переименовать.");
        return;
      }
      // обновляем текущий экран
      load();
    })
    .catch(() => alert("Не удалось переименовать."));
}

// --- Логика Drag & Drop ---
function setupDragAndDrop() {
  const dropZone = document.querySelector(".container");

  // Отменяем стандартное поведение браузера (открытие файла)
  ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, preventDefaults, false);
  });

  function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
  }

  // Визуальная подсветка
  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, () => dropZone.classList.add("drag-over"), false);
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove("drag-over"), false);
  });

  // Обработка сброса файлов
  dropZone.addEventListener("drop", (e) => {
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      upload(files); // Передаем сброшенные файлы в upload
    }
  });
}
// --------------------------

function download(path) {
  window.location = `/api/download?path=${encodeURIComponent(path)}`;
}

// Модифицированная функция upload принимает аргумент (filesFromDrop)
function upload(filesFromDrop = null) {
  // Если переданы файлы через Drop, берем их. Иначе берем из input
  // Важно: filesFromDrop может быть событием click, если вызвана кнопкой, поэтому проверяем тип
  let files;

  if (filesFromDrop && filesFromDrop instanceof FileList) {
    files = filesFromDrop;
  } else {
    files = document.getElementById("fileInput").files;
  }

  if (!files || files.length === 0) return;

  const data = new FormData();
  for (let f of files) data.append("files", f);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/upload?path=${encodeURIComponent(currentPath)}`);

  const statsEl = document.getElementById("upload-stats");
  const setStats = (txt) => {
    if (statsEl) statsEl.textContent = txt || "";
  };

  let lastT = 0;
  let lastLoaded = 0;

  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;

    // progress bar
    const pct = (e.loaded / e.total) * 100;
    document.getElementById("progress-bar").style.width = pct + "%";

    // speed
    const now = performance.now();
    if (!lastT) {
      lastT = now;
      lastLoaded = e.loaded;
      setStats(`Скорость: … | ${formatBytes(e.loaded)} из ${formatBytes(e.total)}`);
      return;
    }

    const dt = (now - lastT) / 1000; // seconds
    const db = e.loaded - lastLoaded;
    if (dt > 0.15) {
      const speed = db / dt; // bytes/s
      setStats(`Скорость: ${formatSpeed(speed)} | ${formatBytes(e.loaded)} из ${formatBytes(e.total)}`);
      lastT = now;
      lastLoaded = e.loaded;
    }
  };

  xhr.onload = () => {
    document.getElementById("progress-bar").style.width = "0%";
    setStats("");
    load();
    // Очищаем input, чтобы можно было загрузить тот же файл повторно через кнопку
    document.getElementById("fileInput").value = "";
  };

  xhr.onerror = () => {
    document.getElementById("progress-bar").style.width = "0%";
    setStats("Ошибка загрузки.");
  };

  xhr.send(data);
}

function createFolder() {
  const name = prompt("Имя папки");
  if (!name) return;

  fetch("/api/create-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: currentPath, name }),
  }).then(load);
}

function del(e, path) {
  e.stopPropagation();
  if (!confirm("Удалить навсегда?")) return;

  fetch("/api/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  }).then(load);
}

function renderBreadcrumbs(path) {
  const container = document.getElementById("breadcrumbs");
  container.innerHTML = "";

  const parts = path ? path.split("/") : [];

  const root = document.createElement("span");
  root.textContent = "DATAHUB";
  root.onclick = () => {
    currentPath = "";
    load();
  };
  container.appendChild(root);

  let accumulated = "";

  parts.forEach((part) => {
    accumulated += (accumulated ? "/" : "") + part;
    const sep = document.createElement("span");
    sep.textContent = " / ";
    container.appendChild(sep);

    const pathForCrumb = accumulated;
    const crumb = document.createElement("span");
    crumb.textContent = part;
    crumb.onclick = () => {
      currentPath = pathForCrumb;
      load();
    };
    container.appendChild(crumb);
  });
}

function renderSearchBreadcrumbs(q) {
  const container = document.getElementById("breadcrumbs");
  container.innerHTML = "";

  const root = document.createElement("span");
  root.textContent = "DATAHUB";
  root.onclick = () => {
    currentPath = "";
    const si = document.getElementById("searchInput");
    if (si) si.value = "";
    searchQuery = "";
    load();
  };
  container.appendChild(root);

  const sep = document.createElement("span");
  sep.textContent = " / ";
  container.appendChild(sep);

  const label = document.createElement("span");
  label.textContent = `Поиск: ${q}`;
  container.appendChild(label);
}

function formatBytes(bytes) {
  const b = Number(bytes || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const digits = i === 0 ? 0 : (i === 1 ? 1 : 2);
  return `${v.toFixed(digits)} ${units[i]}`;
}

function formatSpeed(bytesPerSec) {
  return `${formatBytes(bytesPerSec)}/s`;
}
