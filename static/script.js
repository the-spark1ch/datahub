let currentPath = "";

function load() {
    fetch(`/api/list?path=${currentPath}`)
        .then(r => r.json())
        .then(items => {
            const list = document.getElementById("fileList");
            list.innerHTML = "";

            if (currentPath) {
                const back = document.createElement("li");
                back.textContent = "⬅ Назад";
                back.onclick = () => {
                    currentPath = currentPath.split("/").slice(0, -1).join("/");
                    load();
                };
                list.appendChild(back);
            }

            items.forEach(item => {
                const li = document.createElement("li");

                if (item.is_dir) {
                    li.innerHTML = `
                        <span>📁 ${item.name}</span>
                        <div class="file-actions">
                            <button class="delete" onclick="del(event, '${item.path}')">
                                Удалить
                            </button>
                        </div>
                    `;
                    li.onclick = () => {
                        currentPath = item.path;
                        load();
                    };
                } else {
                    li.innerHTML = `
                        <span>📄 ${item.name}</span>
                        <div class="file-actions">
                            <button onclick="download('${item.path}')">
                                Скачать
                            </button>
                            <button class="delete" onclick="del(event, '${item.path}')">
                                Удалить
                            </button>
                        </div>
                    `;
                }

                list.appendChild(li);
            });
        });
}

function download(path) {
    window.location = `/api/download?path=${path}`;
}

function upload() {
    const files = document.getElementById("fileInput").files;
    const data = new FormData();
    for (let f of files) data.append("files", f);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload?path=${currentPath}`);

    xhr.upload.onprogress = e => {
        if (e.lengthComputable) {
            document.getElementById("progress-bar").style.width =
                (e.loaded / e.total * 100) + "%";
        }
    };

    xhr.onload = () => {
        document.getElementById("progress-bar").style.width = "0%";
        load();
    };

    xhr.send(data);
}

function createFolder() {
    const name = prompt("Имя папки");
    if (!name) return;

    fetch("/api/create-folder", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path: currentPath, name })
    }).then(load);
}

function del(e, path) {
    e.stopPropagation();
    if (!confirm("Удалить навсегда?")) return;

    fetch("/api/delete", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path })
    }).then(load);
}

load();