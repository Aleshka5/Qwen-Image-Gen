(() => {
  const form = document.getElementById("form");
  const fileInput = document.getElementById("images");
  const previews = document.getElementById("previews");
  const statusEl = document.getElementById("status");
  const logsEl = document.getElementById("logs");
  const submit = document.getElementById("submit");
  const result = document.getElementById("result");
  const gallery = document.getElementById("gallery");
  const meta = document.getElementById("meta");
  const storageError = document.getElementById("storage-error");

  const MAX_IMAGES = Number(document.body.dataset.maxImages || 10);
  /** @type {{file: File, url: string}[]} */
  let selected = [];
  let dragFrom = -1;

  const setStatus = (text, isError = false) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", isError);
    statusEl.hidden = !text;
  };

  const move = (from, to) => {
    if (from === to || from < 0 || to < 0 || from >= selected.length || to >= selected.length) {
      return;
    }
    const [item] = selected.splice(from, 1);
    selected.splice(to, 0, item);
    render();
  };

  const render = () => {
    previews.replaceChildren();
    const canReorder = selected.length > 1;
    selected.forEach((item, index) => {
      const number = index + 1;
      const figure = document.createElement("figure");
      figure.className = "preview";
      figure.draggable = canReorder;

      figure.addEventListener("dragstart", (event) => {
        dragFrom = index;
        figure.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(index));
      });
      figure.addEventListener("dragend", () => {
        dragFrom = -1;
        figure.classList.remove("dragging");
      });
      figure.addEventListener("dragover", (event) => {
        if (dragFrom < 0 || dragFrom === index) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        figure.classList.add("drag-over");
      });
      figure.addEventListener("dragleave", () => figure.classList.remove("drag-over"));
      figure.addEventListener("drop", (event) => {
        event.preventDefault();
        figure.classList.remove("drag-over");
        const from = dragFrom >= 0 ? dragFrom : Number(event.dataTransfer.getData("text/plain"));
        move(from, index);
      });

      const badge = document.createElement("span");
      badge.className = "ref-index";
      badge.textContent = String(number);

      const img = document.createElement("img");
      img.src = item.url;
      img.alt = `image ${number}: ${item.file.name}`;

      const caption = document.createElement("figcaption");
      caption.textContent = `image ${number}`;
      figure.append(badge, img, caption);

      if (canReorder) {
        const moves = document.createElement("div");
        moves.className = "preview-moves";
        moves.append(
          moveButton("←", `Сдвинуть image ${number} левее`, index > 0, () => move(index, index - 1)),
          moveButton("→", `Сдвинуть image ${number} правее`, index < selected.length - 1, () => move(index, index + 1)),
        );
        figure.append(moves);
      }

      previews.append(figure);
    });
  };

  const moveButton = (label, aria, enabled, onClick) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "preview-move";
    button.textContent = label;
    button.setAttribute("aria-label", aria);
    button.disabled = !enabled;
    button.draggable = false;
    button.addEventListener("click", onClick);
    button.addEventListener("dragstart", (event) => event.preventDefault());
    return button;
  };

  fileInput.addEventListener("change", () => {
    selected.forEach((item) => URL.revokeObjectURL(item.url));
    const files = [...fileInput.files];
    if (files.length > MAX_IMAGES) {
      setStatus(`Выбрано ${files.length} файлов — оставьте не больше ${MAX_IMAGES}`, true);
    } else {
      setStatus("");
    }
    selected = files.slice(0, MAX_IMAGES).map((file) => ({
      file,
      url: URL.createObjectURL(file),
    }));
    render();
  });

  const readLogs = async (after) => {
    const response = await fetch(`/api/logs?after=${after}`);
    if (!response.ok) return after;
    const payload = await response.json();
    let cursor = after;
    for (const line of payload.lines || []) {
      const row = document.createElement("div");
      row.textContent = line.message;
      if (line.level === "WARNING" || line.level === "ERROR" || line.level === "CRITICAL") {
        row.className = "log-warn";
      }
      logsEl.append(row);
      cursor = line.id;
    }
    if (payload.lines && payload.lines.length) {
      logsEl.hidden = false;
      logsEl.scrollTop = logsEl.scrollHeight;
    }
    return cursor;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    logsEl.replaceChildren();
    logsEl.hidden = true;
    setStatus("Генерация запущена. Первый запрос после старта ждёт загрузки весов — это несколько минут.");

    const body = new FormData(form);
    body.delete("images");
    selected.forEach((item) => body.append("images", item.file, item.file.name));

    let logCursor = 0;
    try {
      const snap = await fetch("/api/logs?after=0");
      if (snap.ok) {
        const prior = await snap.json();
        const last = (prior.lines || []).at(-1);
        if (last) logCursor = last.id;
      }
    } catch {
      logCursor = 0;
    }

    let polling = true;
    const poll = async () => {
      while (polling) {
        try {
          logCursor = await readLogs(logCursor);
        } catch {
          /* сеть моргнула — следующий круг */
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      try {
        await readLogs(logCursor);
      } catch {
        /* итоговые строки уже могли прийти */
      }
    };
    const pollTask = poll();

    const started = performance.now();
    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        body,
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      gallery.replaceChildren();
      payload.images.forEach((item) => {
        const link = document.createElement("a");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener";
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = item.name;
        link.append(img);
        gallery.append(link);
      });

      meta.textContent = `${payload.width}×${payload.height} · seed ${payload.seed} · ${payload.duration} с на GPU`;
      const notSaved = payload.saved === false && typeof payload.storage_error === "string"
        ? payload.storage_error
        : "";
      storageError.textContent = notSaved;
      storageError.hidden = !notSaved;
      result.hidden = false;
      setStatus(`Готово за ${((performance.now() - started) / 1000).toFixed(1)} с`);
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      polling = false;
      await pollTask;
      submit.disabled = false;
    }
  });
})();
