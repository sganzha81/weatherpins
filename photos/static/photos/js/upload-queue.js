(function () {
    "use strict";

    const form = document.getElementById("upload-form");
    const fileInput = document.querySelector("[data-upload-input]");
    const filePickerControl = document.querySelector("[data-upload-picker]");
    const preview = document.querySelector("[data-upload-preview]");
    const previewList = document.querySelector("[data-upload-preview-list]");
    const selectionSummary = document.querySelector("[data-upload-selection-summary]");
    const submitButton = document.querySelector("[data-upload-submit]");
    const loadingNote = document.querySelector("[data-upload-loading-note]");

    if (
        !form ||
        !fileInput ||
        !filePickerControl ||
        !preview ||
        !previewList ||
        !selectionSummary ||
        !submitButton ||
        !loadingNote ||
        typeof DataTransfer === "undefined"
    ) {
        return;
    }

    const selectedFiles = [];
    const maxFileSizeBytes = 10 * 1024 * 1024;

    function fileKey(file) {
        return [file.name, file.size, file.lastModified].join("/");
    }

    function formatSize(size) {
        return (size / 1024 / 1024).toFixed(2) + " МБ";
    }

    function isHeif(file) {
        return /\.(heic|heif)$/i.test(file.name) || /image\/hei[cf]/i.test(file.type);
    }

    function revokePreviewUrl(item) {
        if (item.previewUrl) {
            URL.revokeObjectURL(item.previewUrl);
            item.previewUrl = null;
        }
    }

    function syncInputFiles() {
        const dataTransfer = new DataTransfer();
        selectedFiles.forEach(function (item) {
            dataTransfer.items.add(item.file);
        });
        fileInput.files = dataTransfer.files;
    }

    function updateSelectionSummary() {
        selectionSummary.textContent = "Выбрано: " + selectedFiles.length;
        filePickerControl.textContent = selectedFiles.length
            ? "Добавить ещё"
            : "Выбрать фото";
        submitButton.textContent = selectedFiles.length
            ? "Загрузить " + selectedFiles.length + " фото"
            : "Загрузить фото";
        preview.hidden = selectedFiles.length === 0;
        submitButton.disabled = selectedFiles.length === 0;
    }

    function addPlaceholder(thumb, text) {
        const placeholder = document.createElement("span");
        placeholder.className = "upload-preview-placeholder";
        placeholder.textContent = text;
        thumb.appendChild(placeholder);
    }

    function createPreviewItem(item, index) {
        const listItem = document.createElement("li");
        listItem.className = "upload-preview-item";

        const thumb = document.createElement("div");
        thumb.className = "upload-preview-thumb";
        if (isHeif(item.file)) {
            addPlaceholder(thumb, "Предпросмотр HEIC недоступен");
        } else {
            const image = document.createElement("img");
            const previewUrl = URL.createObjectURL(item.file);
            item.previewUrl = previewUrl;
            image.src = previewUrl;
            image.alt = "Предпросмотр " + item.file.name;
            image.addEventListener("error", function () {
                if (!image.isConnected) {
                    return;
                }
                if (item.previewUrl === previewUrl) {
                    revokePreviewUrl(item);
                }
                image.remove();
                addPlaceholder(thumb, "Предпросмотр недоступен");
            });
            thumb.appendChild(image);
        }

        const details = document.createElement("div");
        details.className = "upload-preview-details";
        const name = document.createElement("span");
        name.className = "upload-preview-name";
        name.textContent = item.file.name;
        details.appendChild(name);

        const size = document.createElement("span");
        size.className = "upload-preview-size";
        size.textContent = formatSize(item.file.size);
        details.appendChild(size);

        if (item.file.size > maxFileSizeBytes) {
            const warning = document.createElement("span");
            warning.className = "upload-preview-warning";
            warning.textContent = "Файл больше 10 МБ.";
            details.appendChild(warning);
        }

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "upload-preview-remove";
        removeButton.textContent = "×";
        removeButton.setAttribute(
            "aria-label",
            "Удалить " + item.file.name + " из загрузки"
        );
        removeButton.addEventListener("click", function () {
            removeFile(index);
        });

        listItem.appendChild(thumb);
        listItem.appendChild(details);
        listItem.appendChild(removeButton);
        return listItem;
    }

    function renderQueue() {
        selectedFiles.forEach(revokePreviewUrl);
        previewList.textContent = "";
        selectedFiles.forEach(function (item, index) {
            previewList.appendChild(createPreviewItem(item, index));
        });
        updateSelectionSummary();
    }

    function removeFile(index) {
        revokePreviewUrl(selectedFiles[index]);
        selectedFiles.splice(index, 1);
        syncInputFiles();
        renderQueue();
        filePickerControl.focus();
    }

    function addFiles(files) {
        Array.from(files).forEach(function (file) {
            const alreadySelected = selectedFiles.some(function (item) {
                return fileKey(item.file) === fileKey(file);
            });
            if (!alreadySelected) {
                selectedFiles.push({ file: file, previewUrl: null });
            }
        });
        syncInputFiles();
        renderQueue();
    }

    fileInput.addEventListener("change", function () {
        addFiles(fileInput.files);
    });

    filePickerControl.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            fileInput.click();
        }
    });

    form.addEventListener("submit", function (event) {
        syncInputFiles();
        if (!selectedFiles.length) {
            event.preventDefault();
            return;
        }

        submitButton.disabled = true;
        submitButton.textContent = "Загрузка…";
        loadingNote.classList.add("is-visible");
    });

    window.addEventListener("pagehide", function () {
        selectedFiles.forEach(revokePreviewUrl);
    });

    updateSelectionSummary();
}());
