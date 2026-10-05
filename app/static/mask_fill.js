(() => {
  const form = document.getElementById("form");
  const fileInput = document.getElementById("image");
  const refInput = document.getElementById("images");
  const previews = document.getElementById("previews");
  const statusEl = document.getElementById("status");
  const logsEl = document.getElementById("logs");
  const submit = document.getElementById("submit");
  const result = document.getElementById("result");
  const gallery = document.getElementById("gallery");
  const meta = document.getElementById("meta");
  const storageError = document.getElementById("storage-error");
  const stage = document.getElementById("mask-stage");
  const empty = document.getElementById("mask-empty");
  const photoName = document.getElementById("photo-name");
  const viewCanvas = document.getElementById("mask-view");
  const viewCtx = viewCanvas.getContext("2d");
  const toolBrush = document.getElementById("tool-brush");
  const toolEraser = document.getElementById("tool-eraser");
  const brushSizeInput = document.getElementById("brush-size");
  const brushSizeValue = document.getElementById("brush-size-value");
  const undoButton = document.getElementById("undo");
  const clearButton = document.getElementById("clear-mask");

  const maskCanvas = document.createElement("canvas");
  const maskCtx = maskCanvas.getContext("2d", { willReadFrequently: true });
  const tintCanvas = document.createElement("canvas");
  const tintCtx = tintCanvas.getContext("2d");

  const MAX_IMAGES = Number(document.body.dataset.maxImages || 10);
  const MAX_REFS = Math.max(0, MAX_IMAGES - 2);
  const MAX_VIEW_SIDE = 960;
  const UNDO_LIMIT = 8;
  const OVERLAY = "rgba(79, 70, 229, 0.5)";

  /** @type {HTMLImageElement | null} */
  let photo = null;
  /** @type {File | null} */
  let photoFile = null;
  let photoUrl = "";
  let loadToken = 0;
  let tool = "brush";
  let drawing = false;
  /** @type {{x: number, y: number} | null} */
  let lastPoint = null;
  /** @type {ImageData[]} */
  let undoStack = [];
  let hasWhite = false;
  let busy = false;
  let redrawFrame = 0;

  /** @type {{file: File, url: string}[]} */
  let selected = [];
  let dragFrom = -1;

  const setStatus = (text, isError = false) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", isError);
    statusEl.hidden = !text;
  };

  const syncGenerate = () => {
    const ready = photo && hasWhite && form.elements.prompt.value.trim();
    submit.disabled = busy || !ready;
    undoButton.disabled = undoStack.length === 0 || drawing || busy;
    clearButton.disabled = !photo || !hasWhite || drawing || busy;
  };

  const setTool = (next) => {
    tool = next;
    toolBrush.setAttribute("aria-pressed", String(next === "brush"));
    toolEraser.setAttribute("aria-pressed", String(next === "eraser"));
    stage.classList.toggle("is-brush", next === "brush");
    stage.classList.toggle("is-eraser", next === "eraser");
  };

  const fitCanvases = (image) => {
    maskCanvas.width = image.naturalWidth;
    maskCanvas.height = image.naturalHeight;
    maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
    const longSide = Math.max(image.naturalWidth, image.naturalHeight);
    const scale = Math.min(1, MAX_VIEW_SIDE / longSide);
    viewCanvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    viewCanvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    tintCanvas.width = viewCanvas.width;
    tintCanvas.height = viewCanvas.height;
  };

  const redraw = () => {
    const width = viewCanvas.width;
    const height = viewCanvas.height;
    viewCtx.clearRect(0, 0, width, height);
    if (!photo) return;
    viewCtx.drawImage(photo, 0, 0, width, height);
    tintCtx.globalCompositeOperation = "source-over";
    tintCtx.clearRect(0, 0, width, height);
    tintCtx.fillStyle = OVERLAY;
    tintCtx.fillRect(0, 0, width, height);
    tintCtx.globalCompositeOperation = "destination-in";
    tintCtx.drawImage(maskCanvas, 0, 0, width, height);
    tintCtx.globalCompositeOperation = "source-over";
    viewCtx.drawImage(tintCanvas, 0, 0);
  };

  const scheduleRedraw = () => {
    if (redrawFrame) return;
    redrawFrame = requestAnimationFrame(() => {
      redrawFrame = 0;
      redraw();
    });
  };

  const refreshWhite = () => {
    if (!maskCanvas.width || !maskCanvas.height) {
      hasWhite = false;
      return;
    }
    const data = maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height).data;
    hasWhite = false;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 0) {
        hasWhite = true;
        break;
      }
    }
  };

  const pushUndo = () => {
    if (!maskCanvas.width || !maskCanvas.height) return;
    try {
      undoStack.push(maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height));
      if (undoStack.length > UNDO_LIMIT) undoStack.shift();
    } catch {
      undoStack = [];
    }
  };

  const brushWidthPx = () => {
    const cssPx = Number(brushSizeInput.value) || 1;
    const rect = viewCanvas.getBoundingClientRect();
    if (!rect.width || !maskCanvas.width) return cssPx;
    return cssPx * (maskCanvas.width / rect.width);
  };

  const canvasPoint = (event) => {
    const rect = viewCanvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * maskCanvas.width,
      y: ((event.clientY - rect.top) / rect.height) * maskCanvas.height,
    };
  };

  const paint = (from, to) => {
    maskCtx.save();
    maskCtx.lineCap = "round";
    maskCtx.lineJoin = "round";
    maskCtx.lineWidth = brushWidthPx();
    if (tool === "eraser") {
      maskCtx.globalCompositeOperation = "destination-out";
      maskCtx.strokeStyle = "#000";
      maskCtx.fillStyle = "#000";
    } else {
      maskCtx.globalCompositeOperation = "source-over";
      maskCtx.strokeStyle = "#fff";
      maskCtx.fillStyle = "#fff";
    }
    maskCtx.beginPath();
    if (from.x === to.x && from.y === to.y) {
      maskCtx.arc(to.x, to.y, maskCtx.lineWidth / 2, 0, Math.PI * 2);
      maskCtx.fill();
    } else {
      maskCtx.moveTo(from.x, from.y);
      maskCtx.lineTo(to.x, to.y);
      maskCtx.stroke();
    }
    maskCtx.restore();
  };

  const resetPhoto = () => {
    loadToken += 1;
    if (photoUrl) URL.revokeObjectURL(photoUrl);
    photoUrl = "";
    photo = null;
    photoFile = null;
    undoStack = [];
    hasWhite = false;
    drawing = false;
    lastPoint = null;
    viewCanvas.hidden = true;
    empty.hidden = false;
    photoName.textContent = "";
    syncGenerate();
  };

  const loadPhoto = (file) => {
    const token = ++loadToken;
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      if (token !== loadToken) {
        URL.revokeObjectURL(url);
        return;
      }
      if (!image.naturalWidth || !image.naturalHeight) {
        URL.revokeObjectURL(url);
        resetPhoto();
        setStatus("Не удалось открыть фото", true);
        return;
      }
      if (photoUrl) URL.revokeObjectURL(photoUrl);
      photo = image;
      photoFile = file;
      photoUrl = url;
      undoStack = [];
      hasWhite = false;
      drawing = false;
      fitCanvases(image);
      empty.hidden = true;
      viewCanvas.hidden = false;
      photoName.textContent = `${file.name} · ${image.naturalWidth}×${image.naturalHeight}`;
      redraw();
      syncGenerate();
    };
    image.onerror = () => {
      if (token !== loadToken) return;
      URL.revokeObjectURL(url);
      resetPhoto();
      setStatus("Не удалось открыть фото", true);
    };
    image.src = url;
  };

  // PNG маски: тот же размер, что у фото. Белое — заливка, чёрное — оставить. Не цветной оверлей.
  const maskPngBlob = () => new Promise((resolve, reject) => {
    const width = maskCanvas.width;
    const height = maskCanvas.height;
    if (!width || !height) {
      reject(new Error("Нет маски"));
      return;
    }
    const out = document.createElement("canvas");
    out.width = width;
    out.height = height;
    const ctx = out.getContext("2d", { willReadFrequently: true });
    const strokes = maskCtx.getImageData(0, 0, width, height);
    const pixels = ctx.createImageData(width, height);
    const src = strokes.data;
    const dst = pixels.data;
    for (let i = 0; i < src.length; i += 4) {
      const value = src[i + 3] > 0 ? 255 : 0;
      dst[i] = value;
      dst[i + 1] = value;
      dst[i + 2] = value;
      dst[i + 3] = 255;
    }
    ctx.putImageData(pixels, 0, 0);
    out.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Не удалось собрать маску"));
    }, "image/png");
  });

  const endStroke = (event) => {
    if (!drawing) return;
    drawing = false;
    lastPoint = null;
    if (event && viewCanvas.hasPointerCapture(event.pointerId)) {
      viewCanvas.releasePointerCapture(event.pointerId);
    }
    if (redrawFrame) {
      cancelAnimationFrame(redrawFrame);
      redrawFrame = 0;
    }
    if (tool === "eraser") refreshWhite();
    redraw();
    syncGenerate();
  };

  const undo = () => {
    if (drawing || busy) return;
    const snap = undoStack.pop();
    if (!snap) return;
    maskCtx.putImageData(snap, 0, 0);
    refreshWhite();
    redraw();
    syncGenerate();
  };

  const clearMask = () => {
    if (!photo || drawing || busy || !hasWhite) return;
    pushUndo();
    maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
    hasWhite = false;
    redraw();
    syncGenerate();
  };

  toolBrush.addEventListener("click", () => setTool("brush"));
  toolEraser.addEventListener("click", () => setTool("eraser"));
  undoButton.addEventListener("click", undo);
  clearButton.addEventListener("click", clearMask);
  brushSizeInput.addEventListener("input", () => {
    brushSizeValue.textContent = brushSizeInput.value;
  });

  viewCanvas.addEventListener("pointerdown", (event) => {
    if (!photo || busy) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    event.preventDefault();
    viewCanvas.setPointerCapture(event.pointerId);
    pushUndo();
    drawing = true;
    lastPoint = canvasPoint(event);
    paint(lastPoint, lastPoint);
    if (tool === "brush") hasWhite = true;
    scheduleRedraw();
    syncGenerate();
  });
  viewCanvas.addEventListener("pointermove", (event) => {
    if (!drawing || !lastPoint) return;
    event.preventDefault();
    const point = canvasPoint(event);
    paint(lastPoint, point);
    lastPoint = point;
    scheduleRedraw();
  });
  viewCanvas.addEventListener("pointerup", endStroke);
  viewCanvas.addEventListener("pointercancel", endStroke);
  viewCanvas.addEventListener("contextmenu", (event) => event.preventDefault());

  window.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z" || event.shiftKey) return;
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    event.preventDefault();
    undo();
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    setStatus("");
    if (!file) {
      resetPhoto();
      return;
    }
    loadPhoto(file);
  });
  form.elements.prompt.addEventListener("input", syncGenerate);

  const move = (from, to) => {
    if (from === to || from < 0 || to < 0 || from >= selected.length || to >= selected.length) {
      return;
    }
    const [item] = selected.splice(from, 1);
    selected.splice(to, 0, item);
    renderRefs();
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

  const renderRefs = () => {
    previews.replaceChildren();
    const canReorder = selected.length > 1;
    selected.forEach((item, index) => {
      const number = index + 3;
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
      badge.textContent = `image ${number}`;

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

  refInput.addEventListener("change", () => {
    selected.forEach((item) => URL.revokeObjectURL(item.url));
    const files = [...refInput.files];
    if (files.length > MAX_REFS) {
      setStatus(`Выбрано ${files.length} файлов — оставьте не больше ${MAX_REFS}`, true);
    } else {
      setStatus("");
    }
    selected = files.slice(0, MAX_REFS).map((file) => ({
      file,
      url: URL.createObjectURL(file),
    }));
    renderRefs();
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
    if (!(photo && photoFile && hasWhite && form.elements.prompt.value.trim())) {
      setStatus("Нужны фото, закрашенная область и промпт на английском", true);
      return;
    }
    endStroke();
    busy = true;
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    logsEl.replaceChildren();
    logsEl.hidden = true;
    setStatus("Заливка запущена. Первый запрос после старта ждёт загрузки весов — это несколько минут.");

    const body = new FormData(form);
    body.delete("image");
    body.delete("images");
    body.delete("mask");
    body.append("image", photoFile, photoFile.name);
    selected.forEach((item) => body.append("images", item.file, item.file.name));

    let logCursor = 0;
    let polling = false;
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
    let pollTask = Promise.resolve();

    const started = performance.now();
    try {
      const blob = await maskPngBlob();
      body.append("mask", blob, "mask.png");

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

      polling = true;
      pollTask = poll();
      const response = await fetch("/api/mask-fill", {
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
      busy = false;
      submit.removeAttribute("aria-busy");
      syncGenerate();
    }
  });

  syncGenerate();
})();
