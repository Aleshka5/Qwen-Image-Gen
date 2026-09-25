(() => {
  const form = document.getElementById("form");
  const fileInput = document.getElementById("images");
  const previews = document.getElementById("previews");
  const statusEl = document.getElementById("status");
  const submit = document.getElementById("submit");
  const result = document.getElementById("result");
  const gallery = document.getElementById("gallery");
  const meta = document.getElementById("meta");

  const MAX_IMAGES = Number(document.body.dataset.maxImages || 10);

  const setStatus = (text, isError = false) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", isError);
    statusEl.hidden = !text;
  };

  fileInput.addEventListener("change", () => {
    previews.replaceChildren();
    const files = [...fileInput.files];
    if (files.length > MAX_IMAGES) {
      setStatus(`Выбрано ${files.length} файлов — оставьте не больше ${MAX_IMAGES}`, true);
    } else {
      setStatus("");
    }
    files.slice(0, MAX_IMAGES).forEach((file) => {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = file.name;
      img.onload = () => URL.revokeObjectURL(img.src);
      previews.append(img);
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    setStatus("Генерация запущена. Первый запрос после старта ждёт загрузки весов — это несколько минут.");

    const started = performance.now();
    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        body: new FormData(form),
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
      submit.disabled = false;
    }
  });
})();
