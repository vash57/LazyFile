(function () {
  "use strict";

  const refs = {
    viewTitle: document.getElementById("view-title"),
    views: {
      workspace: document.getElementById("view-workspace"),
      activity: document.getElementById("view-activity"),
      safety: document.getElementById("view-safety"),
    },
    navItems: Array.from(document.querySelectorAll(".nav-item[data-view]")),
    activityBadge: document.getElementById("activity-badge"),

    aiCard: document.getElementById("ai-card"),
    aiDot: document.getElementById("ai-dot"),
    aiMode: document.getElementById("ai-mode"),
    aiDetail: document.getElementById("ai-detail"),
    aiRefresh: document.getElementById("ai-refresh"),
    engineChip: document.getElementById("engine-chip"),
    engineLabel: document.getElementById("engine-label"),

    scanForm: document.getElementById("scan-form"),
    dirInput: document.getElementById("dir-input"),
    useDownloads: document.getElementById("use-downloads"),
    scanBtn: document.getElementById("scan-btn"),
    scanBtnLabel: document.getElementById("scan-btn-label"),
    scanStatus: document.getElementById("scan-status"),
    recentPaths: document.getElementById("recent-paths"),
    versionLabel: document.getElementById("version-label"),
    warningBanner: document.getElementById("warning-banner"),

    stats: document.getElementById("stats"),
    statFiles: document.getElementById("stat-files"),
    statFilesNote: document.getElementById("stat-files-note"),
    statCategories: document.getElementById("stat-categories"),
    statProposed: document.getElementById("stat-proposed"),
    statConflicts: document.getElementById("stat-conflicts"),
    statConflictCard: document.getElementById("stat-conflict-card"),

    filesCountLabel: document.getElementById("files-count-label"),
    fileSearch: document.getElementById("file-search"),
    categoryFilter: document.getElementById("category-filter"),
    filesEmpty: document.getElementById("files-empty"),
    filesLoading: document.getElementById("files-loading"),
    filesNoResults: document.getElementById("files-no-results"),
    fileTableWrap: document.getElementById("file-table-wrap"),
    fileTbody: document.getElementById("file-tbody"),
    filesFooter: document.getElementById("files-footer"),
    filesShowing: document.getElementById("files-showing"),
    loadMoreFiles: document.getElementById("load-more-files"),

    previewBadge: document.querySelector(".preview-badge"),
    previewEmpty: document.getElementById("preview-empty"),
    previewLoading: document.getElementById("preview-loading"),
    previewBody: document.getElementById("preview-body"),
    previewNote: document.getElementById("preview-note"),
    previewSelectAll: document.getElementById("preview-select-all"),
    previewClear: document.getElementById("preview-clear"),
    aiEnhanceBtn: document.getElementById("ai-enhance-btn"),
    aiEnhanceLabel: document.getElementById("ai-enhance-label"),
    previewStatus: document.getElementById("preview-status"),
    applyResult: document.getElementById("apply-result"),
    previewList: document.getElementById("preview-list"),
    previewListFoot: document.getElementById("preview-list-foot"),
    suggestionsShowing: document.getElementById("suggestions-showing"),
    loadMoreSuggestions: document.getElementById("load-more-suggestions"),
    selectedCount: document.getElementById("selected-count"),
    selectableCount: document.getElementById("selectable-count"),
    dryRunToggle: document.getElementById("dry-run-toggle"),
    switchHint: document.getElementById("switch-hint"),
    applyBtn: document.getElementById("apply-btn"),
    applyBtnLabel: document.getElementById("apply-btn-label"),

    activityLogNote: document.getElementById("activity-log-note"),
    activityRefresh: document.getElementById("activity-refresh"),
    activityLoading: document.getElementById("activity-loading"),
    activityList: document.getElementById("activity-list"),
    activityEmpty: document.getElementById("activity-empty"),

    toastRegion: document.getElementById("toast-region"),
    modal: document.getElementById("modal"),
    modalTitle: document.getElementById("modal-title"),
    modalBody: document.getElementById("modal-body"),
    modalNoteText: document.getElementById("modal-note-text"),
    modalConfirm: document.getElementById("modal-confirm"),
    modalCancel: document.getElementById("modal-cancel"),
    modalClose: document.getElementById("modal-close"),
  };

  const state = {
    config: null,
    ai: null,
    scan: null,
    files: [],
    suggestions: [],
    selected: new Set(),
    filter: { query: "", category: "all" },
    fileLimit: 120,
    suggestionLimit: 120,
    dryRun: true,
    busy: false,
    applying: false,
    aiBusy: false,
    view: "workspace",
    undoableCount: 0,
  };

  const VIEW_TITLES = {
    workspace: "Workspace",
    activity: "Activity",
    safety: "Safety",
  };

  const STORAGE = {
    lastPath: "lazyfile.lastPath",
    recentPaths: "lazyfile.recentPaths",
  };

  let modalResolve = null;

  /* ------------------------------------------------------------------ utils */

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (char) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char];
    });
  }

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_error) {
      /* Storage can be unavailable in private modes. */
    }
  }

  function categorySlug(name) {
    return (
      String(name)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "") || "other"
    );
  }

  function extensionLabel(file) {
    const suffix = String(file.suffix || "");
    const parts = suffix.replace(/^\./, "").split(".");
    const label = parts[parts.length - 1] || "file";
    return label.toUpperCase().slice(0, 4);
  }

  function formatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return value + " B";
    if (value < 1024 * 1024) return (value / 1024).toFixed(value < 10240 ? 1 : 0) + " KB";
    if (value < 1024 * 1024 * 1024) {
      return (value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0) + " MB";
    }
    return (value / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  }

  function directoryLabel(relPath) {
    const parts = String(relPath).split("/");
    parts.pop();
    return parts.length ? parts.join(" / ") : "Folder root";
  }

  function plural(count, singular, pluralForm) {
    return count === 1 ? singular : (pluralForm || singular + "s");
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  async function api(path, options) {
    const request = { method: "GET", headers: {} };
    if (options && options.method) request.method = options.method;
    if (options && options.body !== undefined) {
      request.headers["Content-Type"] = "application/json";
      request.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, request);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      const message =
        payload && payload.error
          ? payload.error
          : "Request failed with HTTP " + response.status + ".";
      throw new Error(message);
    }
    return payload || {};
  }

  function toast(message, tone) {
    const node = document.createElement("div");
    node.className = "toast" + (tone ? " is-" + tone : "");
    node.textContent = message;
    refs.toastRegion.appendChild(node);
    const delay = tone === "error" || tone === "warning" ? 6500 : 4300;
    window.setTimeout(function () {
      node.classList.add("is-leaving");
      window.setTimeout(function () {
        node.remove();
      }, 190);
    }, delay);
  }

  function openModal(options) {
    if (modalResolve) {
      const previous = modalResolve;
      modalResolve = null;
      previous(false);
    }
    refs.modalTitle.textContent = options.title || "Confirm";
    refs.modalBody.textContent = options.body || "";
    refs.modalNoteText.textContent =
      options.note || "Nothing will be deleted or overwritten.";
    refs.modalConfirm.textContent = options.confirmLabel || "Confirm";
    refs.modalConfirm.className = "btn btn-primary";
    refs.modal.hidden = false;
    window.requestAnimationFrame(function () {
      refs.modalConfirm.focus();
    });
    return new Promise(function (resolve) {
      modalResolve = resolve;
    });
  }

  function closeModal(result) {
    if (!modalResolve) {
      refs.modal.hidden = true;
      return;
    }
    const resolve = modalResolve;
    modalResolve = null;
    refs.modal.hidden = true;
    resolve(result);
  }

  function hasSelectedSafe() {
    return state.suggestions.some(function (item) {
      return item.can_apply && state.selected.has(item.id);
    });
  }

  function selectedSafeSuggestions() {
    return state.suggestions.filter(function (item) {
      return item.can_apply && state.selected.has(item.id);
    });
  }

  /* -------------------------------------------------------------- AI status */

  function setAiStatus(status) {
    state.ai = status;
    if (!status) {
      refs.aiDot.className = "status-dot is-offline";
      refs.aiMode.textContent = "AI status unavailable";
      refs.aiDetail.textContent = "Rule-based suggestions are still available.";
    } else if (status.ready) {
      refs.aiDot.className = "status-dot is-ready";
      refs.aiMode.textContent = "Ollama · " + (status.model || "ready");
      refs.aiDetail.textContent =
        "Local model ready. Only names, types, and sizes are sent.";
    } else if (status.available) {
      refs.aiDot.className = "status-dot is-partial";
      refs.aiMode.textContent = "Rules engine active";
      refs.aiDetail.textContent = status.message || "Ollama is running without a usable model.";
    } else {
      refs.aiDot.className = "status-dot is-offline";
      refs.aiMode.textContent = "Rules engine active";
      refs.aiDetail.textContent =
        status.message || "Ollama not detected. Deterministic suggestions are ready.";
    }
    updateEngineChip();
    updateAiButton();
  }

  async function refreshAiStatus() {
    if (refs.aiRefresh.disabled) return;
    refs.aiRefresh.disabled = true;
    refs.aiRefresh.classList.add("is-busy");
    try {
      const status = await api("/api/ai/status");
      setAiStatus(status);
      renderPreview();
      updateEngineChip();
    } catch (error) {
      setAiStatus(null);
      toast("Could not check Ollama: " + error.message, "error");
    } finally {
      refs.aiRefresh.disabled = false;
      refs.aiRefresh.classList.remove("is-busy");
    }
  }

  function updateAiButton() {
    const ready = !!(state.ai && state.ai.ready);
    const disabled =
      !ready || !state.scan || state.busy || state.applying || state.aiBusy;
    refs.aiEnhanceBtn.disabled = disabled;
    refs.aiEnhanceBtn.classList.toggle("is-busy", state.aiBusy);
    refs.aiEnhanceBtn.title = ready
      ? "Ask the configured local Ollama model for validated suggestions."
      : (state.ai && state.ai.message) || "Ollama is not available.";
    if (state.aiBusy) {
      refs.aiEnhanceLabel.textContent = "Asking local model…";
    } else if (state.scan && state.scan.engine === "ollama") {
      refs.aiEnhanceLabel.textContent = "Refresh AI suggestions";
    } else {
      refs.aiEnhanceLabel.textContent = "Enhance with AI";
    }
  }

  function updateEngineChip() {
    if (state.scan && state.scan.engine === "ollama") {
      refs.engineChip.classList.add("is-ai");
      refs.engineLabel.textContent = state.scan.model
        ? "Ollama · " + state.scan.model
        : "AI-assisted preview";
      return;
    }
    refs.engineChip.classList.remove("is-ai");
    if (state.ai && state.ai.ready) {
      refs.engineLabel.textContent = "Rule engine · AI available";
    } else {
      refs.engineLabel.textContent = "Rule engine ready";
    }
  }

  /* ------------------------------------------------------------- scan flow */

  function setLoadingUi(loading) {
    refs.filesEmpty.hidden = loading || !!state.scan;
    refs.filesLoading.hidden = !loading;
    refs.fileTableWrap.hidden = loading || !state.scan;
    refs.filesNoResults.hidden = true;
    refs.filesFooter.hidden = loading || !state.scan;
    refs.previewEmpty.hidden = loading || !!state.scan;
    refs.previewLoading.hidden = !loading;
    refs.previewBody.hidden = loading || !state.scan;
  }

  async function scanFolder(rawPath, options) {
    const opts = options || {};
    const path = String(rawPath || "").trim();
    if (!path) {
      toast("Enter a folder path first.", "warning");
      refs.dirInput.focus();
      return;
    }
    if (state.busy || state.applying) return;

    state.busy = true;
    let progressTimer = null;
    if (!opts.silent) {
      setLoadingUi(true);
      refs.scanBtn.classList.add("is-busy");
      refs.scanBtn.disabled = true;
      refs.scanBtnLabel.textContent = "Scanning…";
      const messages = [
        "Validating the folder…",
        "Reading file metadata…",
        "Applying categorization rules…",
        "Building a safe preview…",
      ];
      let index = 0;
      refs.scanStatus.textContent = messages[0];
      progressTimer = window.setInterval(function () {
        index = Math.min(index + 1, messages.length - 1);
        refs.scanStatus.textContent = messages[index];
      }, 720);
    } else {
      refs.scanStatus.textContent = "Refreshing folder after undo…";
    }
    updatePreviewCounts();
    updateAiButton();

    try {
      const data = await api("/api/scan", {
        method: "POST",
        body: { path: path },
      });
      applyScan(data);
      rememberPath(data.root);
      const fileCount = data.summary.files;
      const folderCount = data.summary.directories_scanned;
      refs.scanStatus.textContent =
        "Scanned " +
        fileCount +
        " " +
        plural(fileCount, "file") +
        " across " +
        folderCount +
        " " +
        plural(folderCount, "folder") +
        ". Review the preview.";
      if (!opts.silent) {
        toast(
          "Scan complete. " +
            data.summary.proposed +
            " " +
            plural(data.summary.proposed, "safe proposal") +
            " ready for review.",
          "success"
        );
      }
    } catch (error) {
      refs.scanStatus.textContent = opts.silent
        ? "Refresh failed."
        : "Scan failed.";
      toast(error.message, "error");
    } finally {
      state.busy = false;
      if (progressTimer) window.clearInterval(progressTimer);
      refs.scanBtn.classList.remove("is-busy");
      refs.scanBtn.disabled = false;
      refs.scanBtnLabel.textContent = "Scan files";
      setLoadingUi(false);
      renderAll();
    }
  }

  function applyScan(data) {
    state.scan = data;
    state.files = Array.isArray(data.files) ? data.files : [];
    state.suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
    state.selected = new Set(
      state.suggestions
        .filter(function (item) {
          return item.can_apply;
        })
        .map(function (item) {
          return item.id;
        })
    );
    state.filter = { query: "", category: "all" };
    refs.fileSearch.value = "";
    state.fileLimit = 120;
    state.suggestionLimit = 120;
    state.dryRun = true;
    refs.dryRunToggle.checked = true;

    clearApplyResult();
    refs.previewStatus.hidden = true;
    refs.previewStatus.textContent = "";
    refs.previewStatus.className = "preview-status";

    renderCategoryOptions();
    updateDryRunUi();
    renderAll();
  }

  function rememberPath(path) {
    if (!path) return;
    storageSet(STORAGE.lastPath, path);
    let recent = [];
    try {
      const parsed = JSON.parse(storageGet(STORAGE.recentPaths) || "[]");
      if (Array.isArray(parsed)) recent = parsed.filter(function (item) {
        return typeof item === "string";
      });
    } catch (_error) {
      recent = [];
    }
    recent = [path].concat(
      recent.filter(function (item) {
        return item !== path;
      })
    ).slice(0, 6);
    storageSet(STORAGE.recentPaths, JSON.stringify(recent));
    renderRecentPaths();
  }

  function renderRecentPaths() {
    let recent = [];
    try {
      const parsed = JSON.parse(storageGet(STORAGE.recentPaths) || "[]");
      if (Array.isArray(parsed)) recent = parsed.filter(function (item) {
        return typeof item === "string";
      });
    } catch (_error) {
      recent = [];
    }
    refs.recentPaths.innerHTML = "";
    recent.forEach(function (path) {
      const option = document.createElement("option");
      option.value = path;
      refs.recentPaths.appendChild(option);
    });
  }

  function renderCategoryOptions() {
    const names = [];
    if (state.scan) {
      state.files.forEach(function (file) {
        if (!names.includes(file.category)) names.push(file.category);
      });
      names.sort();
    } else if (state.config) {
      state.config.categories.forEach(function (category) {
        names.push(category.name);
      });
    }

    refs.categoryFilter.innerHTML = "";
    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All categories";
    refs.categoryFilter.appendChild(all);

    names.forEach(function (name) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      refs.categoryFilter.appendChild(option);
    });

    if (!names.includes(state.filter.category)) {
      state.filter.category = "all";
    }
    refs.categoryFilter.value = state.filter.category;
  }

  /* --------------------------------------------------------------- rendering */

  function renderAll() {
    renderStats();
    renderFiles();
    renderPreview();
    renderWarnings();
    updateEngineChip();
    updateAiButton();
  }

  function renderStats() {
    if (!state.scan) {
      refs.stats.hidden = true;
      return;
    }
    const files = state.files;
    const categories = new Set(files.map(function (file) {
      return file.category;
    }));
    const pending = state.suggestions.filter(function (item) {
      return item.changed && item.status === "proposed" && !item.conflict;
    });
    const conflicts = state.suggestions.filter(function (item) {
      return item.changed && item.conflict && item.status !== "applied";
    });

    refs.stats.hidden = false;
    refs.statFiles.textContent = String(files.length);
    refs.statCategories.textContent = String(categories.size);
    refs.statProposed.textContent = String(pending.length);
    refs.statConflicts.textContent = String(conflicts.length);
    refs.statFilesNote.textContent =
      state.scan.summary.directories_scanned +
      " " +
      plural(state.scan.summary.directories_scanned, "folder") +
      " scanned";
    refs.statConflictCard.classList.toggle("has-conflicts", conflicts.length > 0);
  }

  function fileStatus(file) {
    let suggestion = null;
    for (let i = 0; i < state.suggestions.length; i += 1) {
      if (state.suggestions[i].file_id === file.id) {
        suggestion = state.suggestions[i];
        break;
      }
    }
    if (!suggestion) return { label: "No preview", className: "state-inplace" };
    if (suggestion.status === "applied") {
      return { label: "Moved", className: "state-moved" };
    }
    if (suggestion.conflict) {
      return { label: "Conflict", className: "state-conflict" };
    }
    if (!suggestion.changed) {
      return { label: "In place", className: "state-inplace" };
    }
    return { label: "Ready", className: "state-ready" };
  }

  function suggestionForFile(fileId) {
    for (let i = 0; i < state.suggestions.length; i += 1) {
      if (state.suggestions[i].file_id === fileId) return state.suggestions[i];
    }
    return null;
  }

  function renderFiles() {
    refs.filesLoading.hidden = true;
    if (!state.scan) {
      setFilesEmptyContent(false);
      refs.filesEmpty.hidden = false;
      refs.filesNoResults.hidden = true;
      refs.fileTableWrap.hidden = true;
      refs.filesFooter.hidden = true;
      refs.filesCountLabel.textContent = "—";
      return;
    }

    if (state.files.length === 0) {
      setFilesEmptyContent(true);
      refs.filesEmpty.hidden = false;
      refs.filesNoResults.hidden = true;
      refs.fileTableWrap.hidden = true;
      refs.filesFooter.hidden = true;
      refs.filesCountLabel.textContent = "0 files";
      return;
    }

    const query = state.filter.query.trim().toLowerCase();
    const filtered = state.files.filter(function (file) {
      if (
        state.filter.category !== "all" &&
        file.category !== state.filter.category
      ) {
        return false;
      }
      if (!query) return true;
      return (
        file.name.toLowerCase().includes(query) ||
        file.rel_path.toLowerCase().includes(query) ||
        file.category.toLowerCase().includes(query)
      );
    });

    const total = filtered.length;
    refs.filesCountLabel.textContent =
      total === state.files.length
        ? total + " " + plural(total, "file")
        : total + " of " + state.files.length;

    if (total === 0) {
      refs.filesEmpty.hidden = true;
      refs.filesNoResults.hidden = false;
      refs.fileTableWrap.hidden = true;
      refs.filesFooter.hidden = true;
      return;
    }

    const visible = filtered.slice(0, state.fileLimit);
    const rows = visible.map(function (file) {
      const status = fileStatus(file);
      const slug = categorySlug(file.category);
      return (
        '<tr>' +
        '<td><div class="file-name-cell">' +
        '<span class="file-badge" aria-hidden="true">' + escapeHtml(extensionLabel(file)) + "</span>" +
        '<div class="file-text">' +
        '<span class="file-name" title="' + escapeHtml(file.name) + '">' + escapeHtml(file.name) + "</span>" +
        '<span class="file-path" title="' + escapeHtml(file.rel_path) + '">' + escapeHtml(directoryLabel(file.rel_path)) + "</span>" +
        "</div></div></td>" +
        '<td><span class="cat-chip cat-' + escapeHtml(slug) + '">' + escapeHtml(file.category) + "</span></td>" +
        '<td class="file-size">' + escapeHtml(formatBytes(file.size)) + "</td>" +
        '<td><span class="state-chip ' + status.className + '">' + escapeHtml(status.label) + "</span></td>" +
        "</tr>"
      );
    });

    refs.fileTbody.innerHTML = rows.join("");
    refs.filesEmpty.hidden = true;
    refs.filesNoResults.hidden = true;
    refs.fileTableWrap.hidden = false;
    refs.filesFooter.hidden = false;
    refs.filesShowing.textContent =
      "Showing " + visible.length + " of " + total + " " + plural(total, "file");
    const more = total - visible.length;
    refs.loadMoreFiles.hidden = more <= 0;
    refs.loadMoreFiles.textContent =
      "Show " + Math.min(120, more) + " more";
  }

  function setFilesEmptyContent(scanned) {
    const heading = refs.filesEmpty.querySelector("h4");
    const copy = refs.filesEmpty.querySelector("p");
    if (scanned) {
      heading.textContent = "No visible files here";
      copy.textContent =
        "This folder is empty, or it contains only hidden files and symbolic links. LazyFile leaves those alone.";
    } else {
      heading.textContent = "No folder scanned yet";
      copy.textContent =
        "Pick a folder above. You will see every visible file and where it could go — before anything moves.";
    }
  }

  function renderWarnings() {
    const warnings =
      state.scan && Array.isArray(state.scan.warnings) ? state.scan.warnings : [];
    if (!warnings.length) {
      refs.warningBanner.hidden = true;
      refs.warningBanner.innerHTML = "";
      return;
    }
    const shown = warnings.slice(0, 5);
    const extra = warnings.length - shown.length;
    refs.warningBanner.innerHTML =
      "<strong>Scan notes</strong>" +
      shown.map(function (warning) {
        return "<span>" + escapeHtml(warning) + "</span>";
      }).join("") +
      (extra > 0
        ? "<span>" + extra + " more " + plural(extra, "note") + " omitted.</span>"
        : "");
    refs.warningBanner.hidden = false;
  }

  function renderPreview() {
    if (!state.scan) {
      refs.previewEmpty.hidden = false;
      refs.previewLoading.hidden = true;
      refs.previewBody.hidden = true;
      updateAiButton();
      return;
    }

    refs.previewEmpty.hidden = true;
    refs.previewLoading.hidden = true;
    refs.previewBody.hidden = false;

    const changed = state.suggestions.filter(function (item) {
      return item.changed && item.status !== "applied";
    });

    const safeIds = new Set(
      changed
        .filter(function (item) {
          return item.can_apply;
        })
        .map(function (item) {
          return item.id;
        })
    );
    Array.from(state.selected).forEach(function (id) {
      if (!safeIds.has(id)) state.selected.delete(id);
    });

    const sorted = changed.slice().sort(function (left, right) {
      const conflictDelta = Number(right.conflict) - Number(left.conflict);
      if (conflictDelta) return conflictDelta;
      return left.original_rel.localeCompare(right.original_rel);
    });
    const visible = sorted.slice(0, state.suggestionLimit);

    if (sorted.length === 0) {
      const appliedCount = state.suggestions.filter(function (item) {
        return item.status === "applied";
      }).length;
      refs.previewList.innerHTML =
        '<div class="empty-state compact">' +
        '<div class="empty-illustration small" aria-hidden="true"><svg class="icon"><use href="#i-check"></use></svg></div>' +
        "<h4>" +
        (appliedCount ? "Everything selected is organized" : "Everything is already tidy") +
        "</h4>" +
        "<p>" +
        (appliedCount
          ? "The moves were applied. Undo is available in the result card or Activity."
          : "No category or filename changes are proposed for this folder. You can still ask the local AI for a second opinion if a model is available.") +
        "</p></div>";
    } else {
      refs.previewList.innerHTML = visible.map(renderSuggestion).join("");
    }

    refs.previewListFoot.hidden = sorted.length <= state.suggestionLimit;
    refs.suggestionsShowing.textContent =
      "Showing " + visible.length + " of " + sorted.length + " " + plural(sorted.length, "change");
    refs.loadMoreSuggestions.hidden = sorted.length <= state.suggestionLimit;

    updatePreviewCounts();
    updateDryRunUi();
    updateAiButton();
  }

  function renderSuggestion(suggestion) {
    const checked =
      state.selected.has(suggestion.id) && suggestion.can_apply;
    const classes =
      "suggestion" +
      (checked ? " is-selected" : "") +
      (suggestion.conflict ? " is-conflict" : "");
    const disabled = suggestion.can_apply ? "" : " disabled";
    const sourceLabel = suggestion.source === "ai" ? "AI" : "Rules";
    const sourceClass = suggestion.source === "ai" ? " is-ai" : "";
    const conflict = suggestion.conflict
      ? '<p class="conflict-note"><svg class="icon" aria-hidden="true"><use href="#i-alert"></use></svg>' +
        escapeHtml(suggestion.conflict_reason || "This destination is blocked.") +
        "</p>"
      : "";
    return (
      '<label class="' + classes + '">' +
      '<input type="checkbox" data-suggestion-id="' + escapeHtml(suggestion.id) + '"' +
      (checked ? " checked" : "") + disabled + ">" +
      '<span class="suggestion-content">' +
      '<span class="suggestion-title">' +
      '<span class="from" title="' + escapeHtml(suggestion.original_name) + '">' + escapeHtml(suggestion.original_name) + "</span>" +
      '<span class="arrow" aria-hidden="true"><svg class="icon"><use href="#i-arrow"></use></svg></span>' +
      '<span class="to" title="' + escapeHtml(suggestion.proposed_name) + '">' + escapeHtml(suggestion.proposed_name) + "</span>" +
      "</span>" +
      '<span class="suggestion-meta">' +
      '<span class="destination-chip"><svg class="icon" aria-hidden="true"><use href="#i-folder"></use></svg>' +
      escapeHtml(suggestion.category) + "</span>" +
      '<span class="source-badge' + sourceClass + '">' + sourceLabel + "</span>" +
      "</span>" +
      '<span class="suggestion-reason">' + escapeHtml(suggestion.reason) + "</span>" +
      conflict +
      "</span></label>"
    );
  }

  function updatePreviewCounts() {
    const safe = state.suggestions.filter(function (item) {
      return item.can_apply;
    });
    const safeIds = new Set(safe.map(function (item) {
      return item.id;
    }));
    Array.from(state.selected).forEach(function (id) {
      if (!safeIds.has(id)) state.selected.delete(id);
    });
    const selected = safe.filter(function (item) {
      return state.selected.has(item.id);
    }).length;

    refs.selectedCount.textContent = selected + " selected";
    refs.selectableCount.textContent = String(safe.length);
    const blocked = state.busy || state.applying || state.aiBusy;
    refs.applyBtn.disabled = selected === 0 || blocked;
    refs.applyBtn.classList.toggle("is-busy", state.applying);
    refs.previewSelectAll.disabled = safe.length === 0 || blocked;
    refs.previewClear.disabled = selected === 0 || blocked;

    if (state.applying) {
      refs.applyBtnLabel.textContent = "Applying…";
    } else if (selected === 0) {
      refs.applyBtnLabel.textContent = state.dryRun ? "Simulate moves" : "Apply moves";
    } else {
      refs.applyBtnLabel.textContent =
        (state.dryRun ? "Simulate " : "Apply ") +
        selected +
        " " +
        plural(selected, "move");
    }
  }

  function updateDryRunUi() {
    refs.switchHint.textContent = state.dryRun ? "Simulate only" : "Live moves";
    refs.previewBadge.textContent = state.dryRun ? "Dry run" : "Live mode";
    refs.previewBadge.classList.toggle("is-live", !state.dryRun);
    const note = refs.previewNote.querySelector("span");
    if (note) {
      note.textContent = state.dryRun
        ? "Nothing has moved. Dry run is on by default."
        : "Live mode: only selected files move, after confirmation.";
    }
  }

  function showPreviewStatus(message, tone) {
    if (!message) {
      refs.previewStatus.hidden = true;
      refs.previewStatus.textContent = "";
      refs.previewStatus.className = "preview-status";
      return;
    }
    refs.previewStatus.hidden = false;
    refs.previewStatus.className =
      "preview-status" +
      (tone === "warning" ? " is-warning" : "") +
      (tone === "loading" ? " is-loading" : "");
    refs.previewStatus.textContent = message;
  }

  /* ------------------------------------------------------------- apply flow */

  function clearApplyResult() {
    refs.applyResult.hidden = true;
    refs.applyResult.innerHTML = "";
    refs.applyResult.className = "apply-result";
  }

  function showApplyResult(data) {
    const summary = data.summary || {};
    const dry = !!data.dry_run;
    const moved = Number(summary.moved || 0);
    const would = Number(
      summary.would_move !== undefined ? summary.would_move : moved
    );
    const skipped = Number(summary.skipped || 0);
    const failed = Number(summary.failed || 0);
    let title;
    let body;
    let tone;

    if (dry) {
      title = "Dry run complete";
      body =
        (would > 0
          ? "Would move " + would + " " + plural(would, "file") + ". "
          : "No moves would be made. ") +
        (skipped ? skipped + " skipped. " : "") +
        (failed ? failed + " could not be simulated. " : "") +
        "Nothing on disk was changed.";
      tone = failed ? "is-warning" : would ? "is-success" : "";
    } else {
      title =
        moved > 0
          ? "Moved " + moved + " " + plural(moved, "file")
          : "No files were moved";
      body =
        "Skipped " + skipped + " · Failed " + failed + ". " +
        "Files can be restored from Activity when the destination still matches.";
      tone = failed || moved === 0 ? "is-warning" : "is-success";
    }

    let actions = "";
    if (!dry && moved > 0 && data.operation_id) {
      const root = state.scan ? state.scan.root : "";
      actions +=
        '<button class="text-btn" type="button" data-undo-op="' +
        escapeHtml(data.operation_id) +
        '" data-undo-root="' +
        escapeHtml(root) +
        '">Undo this run</button>';
    }
    actions +=
      '<button class="text-btn" type="button" data-goto-activity>View activity</button>';

    refs.applyResult.className = "apply-result " + tone;
    refs.applyResult.innerHTML =
      '<div class="apply-result-head"><strong>' + escapeHtml(title) + "</strong></div>" +
      "<p>" + escapeHtml(body) + "</p>" +
      '<div class="result-actions">' + actions + "</div>";
    refs.applyResult.hidden = false;
  }

  async function performApply(dryRun) {
    if (state.applying || state.busy || !state.scan) return;
    const safe = selectedSafeSuggestions();
    if (!safe.length) {
      toast("Select at least one safe move.", "warning");
      return;
    }
    const ids = safe.map(function (item) {
      return item.id;
    });

    state.applying = true;
    clearApplyResult();
    updatePreviewCounts();
    updateAiButton();

    try {
      const data = await api("/api/apply", {
        method: "POST",
        body: {
          scan_id: state.scan.scan_id,
          ids: ids,
          dry_run: dryRun,
        },
      });

      if (!dryRun) {
        const moved = new Set(
          (data.results || [])
            .filter(function (item) {
              return item.status === "moved";
            })
            .map(function (item) {
              return item.id;
            })
        );
        state.suggestions.forEach(function (item) {
          if (moved.has(item.id)) {
            item.status = "applied";
            state.selected.delete(item.id);
          }
        });
      }

      showApplyResult(data);
      if (dryRun) {
        const count = Number(data.summary.moved || 0);
        toast(
          "Dry run simulated " + count + " " + plural(count, "move") + ".",
          count ? "success" : "warning"
        );
      } else {
        const count = Number(data.summary.moved || 0);
        toast(
          count
            ? "Moved " + count + " " + plural(count, "file") + "."
            : "No files were moved. Review the skipped items.",
          count ? "success" : "warning"
        );
        if (count > 0) void loadActivity({ quiet: true });
      }
      renderStats();
      renderFiles();
      renderPreview();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      state.applying = false;
      renderStats();
      renderFiles();
      renderPreview();
      updatePreviewCounts();
      updateAiButton();
    }
  }

  async function onApplyClick() {
    if (!hasSelectedSafe()) {
      toast("Select at least one safe move.", "warning");
      return;
    }
    if (state.dryRun) {
      await performApply(true);
      return;
    }
    const count = selectedSafeSuggestions().length;
    const confirmed = await openModal({
      title: "Apply these moves?",
      body:
        "LazyFile will move " +
        count +
        " selected " +
        plural(count, "file") +
        " into category folders inside the scanned folder. Files that changed or collided since the preview are skipped.",
      note:
        "No files are deleted. Existing files are never overwritten. This run is journaled and can be undone.",
      confirmLabel: "Apply " + count + " " + plural(count, "move"),
    });
    if (confirmed) await performApply(false);
  }

  async function enhanceWithAi() {
    if (!state.scan || state.aiBusy || !state.ai || !state.ai.ready) {
      toast(
        (state.ai && state.ai.message) || "No local AI model is ready.",
        "warning"
      );
      return;
    }
    state.aiBusy = true;
    clearApplyResult();
    showPreviewStatus(
      "Asking " + (state.ai.model || "the local model") + " for suggestions. This can take a moment on a small machine.",
      "loading"
    );
    updatePreviewCounts();
    updateAiButton();
    try {
      const data = await api("/api/suggest", {
        method: "POST",
        body: { scan_id: state.scan.scan_id },
      });
      if (Array.isArray(data.suggestions)) {
        state.suggestions = data.suggestions;
      }
      if (state.scan) {
        state.scan.engine = data.engine || state.scan.engine;
        state.scan.model = data.model || state.scan.model;
      }
      if (data.ok) {
        state.selected = new Set(
          state.suggestions
            .filter(function (item) {
              return item.can_apply;
            })
            .map(function (item) {
              return item.id;
            })
        );
        state.suggestionLimit = 120;
        showPreviewStatus(data.message || "AI suggestions applied.", "");
        toast(data.message || "AI suggestions applied.", "success");
        if (Array.isArray(data.warnings) && data.warnings.length) {
          showPreviewStatus(
            (data.message || "AI suggestions applied.") + " " + data.warnings.join(" "),
            "warning"
          );
        }
      } else {
        showPreviewStatus(
          data.message || "AI suggestions were not applied. Rules remain active.",
          "warning"
        );
        toast(
          data.message || "AI suggestions were not applied.",
          "warning"
        );
      }
      renderStats();
      renderFiles();
      renderPreview();
      updateEngineChip();
    } catch (error) {
      showPreviewStatus(error.message, "warning");
      toast(error.message, "error");
    } finally {
      state.aiBusy = false;
      renderPreview();
      updatePreviewCounts();
      updateAiButton();
    }
  }

  /* ------------------------------------------------------------ activity/undo */

  async function loadActivity(options) {
    const quiet = !!(options && options.quiet);
    if (!quiet) {
      refs.activityLoading.hidden = false;
      refs.activityEmpty.hidden = true;
      refs.activityRefresh.disabled = true;
    }
    try {
      const data = await api("/api/operations?limit=50");
      renderOperations(data);
    } catch (error) {
      refs.activityLogNote.textContent = "Could not load the operation journal.";
      if (!quiet) toast(error.message, "error");
      if (!refs.activityList.children.length) {
        refs.activityEmpty.hidden = false;
      }
    } finally {
      refs.activityLoading.hidden = true;
      refs.activityRefresh.disabled = false;
    }
  }

  function statusLabel(status) {
    const labels = {
      completed: "Completed",
      partial: "Partial",
      interrupted: "Interrupted",
      failed: "Failed",
      undone: "Undone",
      partially_undone: "Partially undone",
      undo_failed: "Undo blocked",
      in_progress: "In progress",
      unknown: "Unknown",
    };
    return labels[status] || status;
  }

  function renderOperations(data) {
    const operations = Array.isArray(data.operations) ? data.operations : [];
    refs.activityLogNote.textContent =
      operations.length +
      " " +
      plural(operations.length, "operation") +
      " · journal " +
      (data.durable ? "on disk" : "not writable") +
      (data.log_error ? " (" + data.log_error + ")" : "");
    refs.activityEmpty.hidden = operations.length > 0;
    const undoable = operations.filter(function (operation) {
      return operation.undoable;
    }).length;
    updateActivityBadge(undoable);

    if (!operations.length) {
      refs.activityList.innerHTML = "";
      return;
    }

    refs.activityList.innerHTML = operations.map(function (operation) {
      const items = Array.isArray(operation.items) ? operation.items : [];
      const undone = Array.isArray(operation.undo_items)
        ? operation.undo_items.filter(function (item) {
            return item.status === "restored" || item.status === "already_restored";
          }).length
        : 0;
      const shownItems = items.slice(0, 30);
      const paths = shownItems.map(function (item) {
        return (
          "<li title=\"" + escapeHtml(item.src) + "\">" +
          escapeHtml(item.src) +
          " → " +
          escapeHtml(item.dst) +
          "</li>"
        );
      }).join("");
      const details = items.length
        ? "<details><summary>Show " + items.length + " moved " + plural(items.length, "path") + "</summary><ul class=\"activity-paths\">" +
          paths +
          (items.length > shownItems.length
            ? "<li>…and " + (items.length - shownItems.length) + " more</li>"
            : "") +
          "</ul></details>"
        : "";
      const undoButton = operation.undoable
        ? '<button class="btn btn-ghost" type="button" data-undo-op="' +
          escapeHtml(operation.id) +
          '" data-undo-root="' +
          escapeHtml(operation.root) +
          '"><svg class="icon"><use href="#i-undo"></use></svg>Undo</button>'
        : "";
      return (
        '<article class="activity-item">' +
        '<div class="activity-head">' +
        '<span class="status-pill status-' + escapeHtml(operation.status) + '">' + escapeHtml(statusLabel(operation.status)) + "</span>" +
        "<h3>" + items.length + " " + plural(items.length, "file") + " moved</h3>" +
        "</div>" +
        '<div class="activity-meta"><span class="activity-root" title="' + escapeHtml(operation.root) + '">' + escapeHtml(operation.root) + "</span><span>·</span><span>" + escapeHtml(formatDate(operation.started_at)) + "</span></div>" +
        '<div class="activity-summary">' +
        '<span class="summary-pill">Planned <strong>' + Number(operation.planned || 0) + "</strong></span>" +
        '<span class="summary-pill">Completed <strong>' + items.length + "</strong></span>" +
        '<span class="summary-pill">Restored <strong>' + undone + "</strong></span>" +
        "</div>" +
        details +
        (undoButton ? '<div class="activity-actions">' + undoButton + "</div>" : "") +
        "</article>"
      );
    }).join("");
  }

  function updateActivityBadge(count) {
    state.undoableCount = count;
    refs.activityBadge.hidden = count <= 0;
    refs.activityBadge.textContent = String(count);
  }

  async function undoOperation(operationId, root, trigger) {
    if (!operationId) return;
    const confirmed = await openModal({
      title: "Undo this organization run?",
      body:
        "LazyFile will move completed files from their category folders back to their original paths. Files that changed or whose original path is now occupied are skipped.",
      note:
        "Undo uses the same no-overwrite rules as apply. It never deletes files.",
      confirmLabel: "Undo moves",
    });
    if (!confirmed) return;

    if (trigger) trigger.disabled = true;
    try {
      const data = await api("/api/undo", {
        method: "POST",
        body: { operation_id: operationId },
      });
      const summary = data.summary || {};
      const restored = Number(summary.restored || 0);
      const already = Number(summary.already_restored || 0);
      const blocked = Number(summary.blocked || 0);
      const failed = Number(summary.failed || 0);
      toast(
        "Undo: " +
          (restored + already) +
          " " +
          plural(restored + already, "file") +
          " restored" +
          (blocked + failed
            ? " · " + (blocked + failed) + " left in place"
            : "."),
        blocked + failed ? "warning" : "success"
      );
      await loadActivity({ quiet: true });
      if (state.scan && root && state.scan.root === root) {
        await scanFolder(root, { silent: true });
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      if (trigger) trigger.disabled = false;
    }
  }

  /* ------------------------------------------------------------------- views */

  function switchView(view) {
    if (!refs.views[view]) return;
    state.view = view;
    Object.keys(refs.views).forEach(function (name) {
      refs.views[name].hidden = name !== view;
    });
    refs.navItems.forEach(function (item) {
      item.classList.toggle("is-active", item.dataset.view === view);
    });
    refs.viewTitle.textContent = VIEW_TITLES[view] || "Workspace";
    window.scrollTo(0, 0);
    if (view === "activity") void loadActivity();
  }

  /* ---------------------------------------------------------------- events */

  function bindEvents() {
    refs.scanForm.addEventListener("submit", function (event) {
      event.preventDefault();
      void scanFolder(refs.dirInput.value);
    });

    refs.useDownloads.addEventListener("click", function () {
      const target =
        state.config && state.config.default_downloads
          ? state.config.default_downloads
          : "";
      if (!target) {
        toast("Could not determine a Downloads folder.", "warning");
        return;
      }
      refs.dirInput.value = target;
      refs.dirInput.focus();
    });

    refs.fileSearch.addEventListener("input", function () {
      state.filter.query = refs.fileSearch.value;
      state.fileLimit = 120;
      renderFiles();
    });

    refs.categoryFilter.addEventListener("change", function () {
      state.filter.category = refs.categoryFilter.value;
      state.fileLimit = 120;
      renderFiles();
    });

    refs.loadMoreFiles.addEventListener("click", function () {
      state.fileLimit += 120;
      renderFiles();
    });

    refs.loadMoreSuggestions.addEventListener("click", function () {
      state.suggestionLimit += 120;
      renderPreview();
    });

    refs.previewList.addEventListener("change", function (event) {
      const input = event.target.closest("input[data-suggestion-id]");
      if (!input) return;
      const id = input.dataset.suggestionId;
      if (input.checked) state.selected.add(id);
      else state.selected.delete(id);
      const card = input.closest(".suggestion");
      if (card) card.classList.toggle("is-selected", input.checked);
      clearApplyResult();
      updatePreviewCounts();
    });

    refs.previewSelectAll.addEventListener("click", function () {
      state.selected = new Set(
        state.suggestions
          .filter(function (item) {
            return item.can_apply;
          })
          .map(function (item) {
            return item.id;
          })
      );
      clearApplyResult();
      renderPreview();
    });

    refs.previewClear.addEventListener("click", function () {
      state.selected.clear();
      clearApplyResult();
      renderPreview();
    });

    refs.dryRunToggle.addEventListener("change", function () {
      state.dryRun = refs.dryRunToggle.checked;
      updateDryRunUi();
      updatePreviewCounts();
    });

    refs.applyBtn.addEventListener("click", function () {
      void onApplyClick();
    });

    refs.aiEnhanceBtn.addEventListener("click", function () {
      void enhanceWithAi();
    });

    refs.aiRefresh.addEventListener("click", function () {
      void refreshAiStatus();
    });

    refs.activityRefresh.addEventListener("click", function () {
      void loadActivity();
    });

    refs.navItems.forEach(function (item) {
      item.addEventListener("click", function () {
        switchView(item.dataset.view);
      });
    });

    refs.modalConfirm.addEventListener("click", function () {
      closeModal(true);
    });
    refs.modalCancel.addEventListener("click", function () {
      closeModal(false);
    });
    refs.modalClose.addEventListener("click", function () {
      closeModal(false);
    });
    refs.modal.addEventListener("click", function (event) {
      if (event.target === refs.modal) closeModal(false);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !refs.modal.hidden) closeModal(false);
    });

    document.addEventListener("click", function (event) {
      const undoButton = event.target.closest("[data-undo-op]");
      if (undoButton) {
        event.preventDefault();
        void undoOperation(
          undoButton.dataset.undoOp,
          undoButton.dataset.undoRoot || "",
          undoButton
        );
        return;
      }
      const gotoActivity = event.target.closest("[data-goto-activity]");
      if (gotoActivity) {
        event.preventDefault();
        switchView("activity");
      }
    });
  }

  /* -------------------------------------------------------------------- init */

  async function init() {
    bindEvents();
    renderRecentPaths();
    renderCategoryOptions();
    updateDryRunUi();
    updatePreviewCounts();
    renderFiles();
    renderPreview();

    const lastPath = storageGet(STORAGE.lastPath);
    if (lastPath) refs.dirInput.value = lastPath;

    const results = await Promise.allSettled([
      api("/api/config"),
      api("/api/ai/status"),
    ]);

    if (results[0].status === "fulfilled") {
      state.config = results[0].value;
      refs.versionLabel.textContent = state.config.version || "1.0.0";
      if (!refs.dirInput.value && state.config.default_downloads) {
        refs.dirInput.value = state.config.default_downloads;
      }
      renderCategoryOptions();
      updateDryRunUi();
    } else {
      toast(
        "Could not load LazyFile configuration: " + results[0].reason.message,
        "error"
      );
    }

    if (results[1].status === "fulfilled") {
      setAiStatus(results[1].value);
    } else {
      setAiStatus(null);
    }

    updateEngineChip();
    updateAiButton();
    void loadActivity({ quiet: true });
  }

  void init();
})();
