(() => {
  const form = document.getElementById("form");
  const fileInput = document.getElementById("image");
  const previews = document.getElementById("previews");
  const statusEl = document.getElementById("status");
  const logsEl = document.getElementById("logs");
  const submit = document.getElementById("submit");
  const result = document.getElementById("result");
  const gallery = document.getElementById("gallery");
  const meta = document.getElementById("meta");

  let previewUrl = "";

  const setStatus = (text, isError = false) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", isError);
    statusEl.hidden = !text;
  };

  const render = () => {
    previews.replaceChildren();
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    const figure = document.createElement("figure");
    figure.className = "preview";
    const img = document.createElement("img");
    img.src = previewUrl;
    img.alt = file.name;
    const caption = document.createElement("figcaption");
    caption.textContent = file.name;
    figure.append(img, caption);
    previews.append(figure);
  };

  fileInput.addEventListener("change", () => {
    setStatus("");
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
    if (!fileInput.files || !fileInput.files[0]) {
      setStatus("Нужно одно референсное фото", true);
      return;
    }
    submit.disabled = true;
    logsEl.replaceChildren();
    logsEl.hidden = true;
    setStatus("Правка запущена. Первый запрос после старта ждёт загрузки весов — это несколько минут.");

    const body = new FormData(form);

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
      const response = await fetch("/api/customize", {
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
