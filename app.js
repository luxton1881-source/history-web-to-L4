// ==========================================================================
// 1. ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ
// ==========================================================================
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

// Идентификаторы Google Таблиц для прямого чтения через GViz API
const SPREADSHEET_ID = "1PfWdhYCPCM4zbhV76qk5wcXIfQZzU7qSRwLlxkN7Jx0";
const CRAMER_SPREADSHEET_ID = "1esH5e9nWVuE0ZtKc3TvKxSGGKweBoNEeetnREgoekyg";

// Получение параметров uid и region из строки запроса текущего URL
const urlParams = new URLSearchParams(window.location.search);
const SERVER_UID = urlParams.get('uid') || "";
const SERVER_REGION = urlParams.get('region') || "";

// Глобальные переменные данных
let JOBS_STD = [];
let JOBS_AVR = [];
let selectedAvr = [];
let stockItems = [];
let cityCodesData = [];
let scanner = null;
let tkdDebounceTimer = null;
let currentScanId = null;
let cropper = null;
let isSubmitting = false;

// Вспомогательная функция парсинга ответа Google GViz API
function parseGvizResponse(text) {
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start === -1 || end === -1) throw new Error("Неверный формат ответа GViz");
    return JSON.parse(text.substring(start, end + 1));
}

// Запуск при загрузке страницы
window.onload = function() {
    // 1. Установка региона в шапке
    const regBadge = document.getElementById('badge-region');
    if (SERVER_REGION && SERVER_REGION !== "undefined") {
        regBadge.innerText = "📍 " + SERVER_REGION;
    } else {
        regBadge.innerText = "⚠️ Регион не задан";
        regBadge.style.color = "red";
    }

    // 2. Блокировка кнопки отправки на время загрузки справочников
    const sendBtn = document.getElementById('send-btn');
    sendBtn.disabled = true;
    sendBtn.innerText = "ЗАГРУЗКА ДАННЫХ...";

    // 3. Прямая загрузка справочников и пользователя из листа «Данные»
    const gvizUrl = `https://docs.google.com/spreadsheets/d/${SPREADSHEET_ID}/gviz/tq?tqx=out:json&sheet=${encodeURIComponent('Данные')}`;

    fetch(gvizUrl)
        .then(r => r.text())
        .then(text => {
            const data = parseGvizResponse(text);
            const rows = (data.table && data.table.rows) || [];

            let foundMasterFio = "";
            let stdSet = new Set();
            let avrSet = new Set();
            let cityCodesList = [];

            // Обрабатываем каждую строку листа «Данные»
            for (let i = 0; i < rows.length; i++) {
                const c = rows[i].c;
                if (!c) continue;

                // Поиск мастера: колонка C (индекс 2) - ФИО, колонка D (индекс 3) - ID ТГ
                if (c[3] && c[3].v !== null && String(c[3].v).trim() === String(SERVER_UID).trim()) {
                    if (c[2] && c[2].v) {
                        foundMasterFio = String(c[2].v).trim();
                    }
                }

                // Стандартные работы: колонка I (индекс 8)
                if (c[8] && c[8].v !== null) {
                    let job = String(c[8].v).trim();
                    if (job && job !== "Виды работ") stdSet.add(job);
                }

                // АВР работы: колонка J (индекс 9)
                if (c[9] && c[9].v !== null) {
                    let avr = String(c[9].v).trim();
                    if (avr && avr !== "АВР") avrSet.add(avr);
                }

                // Коды городов: колонка M (индекс 12) - Область, колонка N (индекс 13) - Код города
                if (c[12] && c[13] && c[12].v !== null && c[13].v !== null) {
                    let reg = String(c[12].v).trim();
                    let code = String(c[13].v).trim();
                    if (reg && code && code !== "Код города") {
                        cityCodesList.push({ region: reg, code: code });
                    }
                }
            }

            // Установка фамилии мастера
            const userBadge = document.getElementById('badge-user');
            if (foundMasterFio) {
                let surname = foundMasterFio.split(" ")[0];
                userBadge.innerText = "👤 " + surname;
                document.getElementById('stock-title').innerText = "Склад: " + surname;
            } else if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
                userBadge.innerText = "👤 " + (tg.initDataUnsafe.user.first_name || "Мастер");
            } else {
                userBadge.innerText = "👤 Мастер";
            }

            // Заполнение справочников
            JOBS_STD = Array.from(stdSet);
            JOBS_AVR = Array.from(avrSet);
            cityCodesData = cityCodesList;

            // Отрисовка сетки работ на форме
            renderMainGrid();

            // Разблокировка кнопки отправки
            sendBtn.disabled = false;
            sendBtn.innerText = "ОТПРАВИТЬ ОТЧЕТ";
        })
        .catch(err => {
            console.error("Ошибка загрузки данных из Google Таблицы:", err);
            sendBtn.innerText = "ОШИБКА ЗАГРУЗКИ";
            alert("Не удалось загрузить данные из Google Таблицы. Проверьте интернет-соединение.");
        });
};

// ==========================================================================
// 2. ЛОГИКА ТКД И АВТОПОДСТАНОВКИ АДРЕСА
// ==========================================================================

// Отслеживание изменений в поле ТКД для автоматической очистки через 2 секунды
function onTkdInputChange(el) {
    clearTimeout(tkdDebounceTimer);
    tkdDebounceTimer = setTimeout(() => {
        applyTkdCleaning(el);
    }, 2000);
}

// Очистка и приведение названия ТКД к правильному формату
function applyTkdCleaning(el) {
    let val = el.value.trim();
    if (!val) return;
    
    // Регулярное выражение для ТКД
    let regex = /([A-Z0-9]{3}_[A-Z0-9]{3}\d{5}_\d{4,5}(?:_\d)?)/i;
    let match = val.match(regex);
    if (match) {
        el.value = match[1].toUpperCase();
        searchAddress();
    }
}

// Очистка поля ТКД
function clearTkdInput() {
    document.getElementById('tkd').value = "";
    document.getElementById('address-label').innerText = "";
}

// Поиск адреса ТКД напрямую по базе Крамера через Google GViz API
function searchAddress() {
    let v = document.getElementById('tkd').value.trim();
    if (v.length < 4) return;

    // Извлекаем чистый базовый ТКД без суффикса свича (_1, _2)
    let match = v.match(/^([A-Za-z0-9]{3}_[A-Za-z0-9]{3}\d{5}_\d{4,5})/);
    let cleanTkd = match ? match[1].toUpperCase() : v.toUpperCase();

    let label = document.getElementById('address-label');
    label.innerText = "🔄 Поиск адреса...";

    // Точечный SQL-запрос к листу «Крамер»: ищем в колонке Q и возвращаем колонку G
    const query = encodeURIComponent(`SELECT G WHERE Q = '${cleanTkd}' LIMIT 1`);
    const cramerUrl = `https://docs.google.com/spreadsheets/d/${CRAMER_SPREADSHEET_ID}/gviz/tq?tqx=out:json&sheet=${encodeURIComponent('Крамер')}&tq=${query}`;

    fetch(cramerUrl)
        .then(r => r.text())
        .then(text => {
            const data = parseGvizResponse(text);
            const rows = (data.table && data.table.rows) || [];
            if (rows.length > 0 && rows[0].c && rows[0].c[0] && rows[0].c[0].v) {
                label.innerText = "🏠 " + rows[0].c[0].v;
            } else {
                label.innerText = "Адрес не найден";
            }
        })
        .catch(err => {
            console.error("Ошибка при поиске адреса:", err);
            label.innerText = "Ошибка загрузки адреса";
        });
}

// ==========================================================================
// 3. КОНСТРУКТОР ТКД
// ==========================================================================

function openConstructor() {
    let citySelect = document.getElementById('constr-city');
    citySelect.innerHTML = '<option value="">Выберите код...</option>';
    
    // Регистронезависимая фильтрация кодов городов для текущего региона (SERVER_REGION)
    let currentReg = (SERVER_REGION || "").trim().toLowerCase();
    let filtered = cityCodesData.filter(row => {
        let r = (row.region || "").trim().toLowerCase();
        return r === currentReg || r.includes(currentReg) || currentReg.includes(r);
    });

    // Если по фильтру ничего не найдено, показываем все доступные коды городов
    let listToShow = filtered.length > 0 ? filtered : cityCodesData;
    listToShow.forEach(item => {
        citySelect.innerHTML += `<option value="${item.code}">${item.code} (${item.region})</option>`;
    });
    
    document.getElementById('modal-constructor').style.display = 'flex';
    updateConstrPreview();
}

function updateConstrPreview() {
    let city = document.getElementById('constr-city').value;
    let mdu = document.getElementById('constr-mdu').value;
    let tkd = document.getElementById('constr-tkd-num').value;
    let sw = document.getElementById('constr-sw-num').value;
    
    let result = city || "MDU_???";
    if (mdu) {
        result += String(mdu).padStart(5, '0') + "_";
    }
    if (tkd) {
        result += tkd;
    }
    if (sw) {
        result += "_" + sw;
    }
    document.getElementById('constr-preview').innerText = result;
}

function saveConstructor() {
    let finalName = document.getElementById('constr-preview').innerText;
    if (finalName.includes('?') || !document.getElementById('constr-city').value) {
        alert("Заполните обязательные поля!");
        return;
    }
    document.getElementById('tkd').value = finalName;
    closeConstructor();
    searchAddress();
}

function clearConstructor() {
    document.getElementById('constr-city').value = "";
    document.getElementById('constr-mdu').value = "";
    document.getElementById('constr-tkd-num').value = "";
    document.getElementById('constr-sw-num').value = "";
    updateConstrPreview();
}

function closeConstructor() {
    document.getElementById('modal-constructor').style.display = 'none';
}

// ==========================================================================
// 4. СЕТКА ВИДОВ РАБОТ
// ==========================================================================

function renderMainGrid() {
    let container = document.getElementById('works-container');
    container.innerHTML = "";
    
    // Создаем кнопку АВР на первом месте
    let avrBtn = document.createElement('div');
    avrBtn.id = 'avr-main-btn';
    avrBtn.className = 'grid-item avr';
    avrBtn.innerHTML = `<span>⚡</span><span>АВР</span>`;
    avrBtn.onclick = openAvrModal;
    container.appendChild(avrBtn);
    
    // Рендерим стандартные работы из пришедшего списка
    if (JOBS_STD) {
        JOBS_STD.forEach(job => {
            let div = document.createElement('div');
            div.className = 'grid-item';
            div.innerHTML = `<input type="checkbox" name="std_job" value="${job}"><span>${job}</span>`;
            div.onclick = (e) => {
                if (e.target.type !== 'checkbox') {
                    let chk = div.querySelector('input');
                    chk.checked = !chk.checked;
                }
                div.classList.toggle('checked', div.querySelector('input').checked);
                updateSummary();
            };
            container.appendChild(div);
        });
    }
}

function updateSummary() {
    let arr = [];
    document.querySelectorAll('input[name="std_job"]:checked').forEach(el => arr.push(el.value));
    arr = arr.concat(selectedAvr);
    document.getElementById('works-text').innerText = arr.join(', ');
}

// Модальное окно для выбора АВР
function openAvrModal() {
    let container = document.getElementById('avr-list-container');
    container.innerHTML = "";
    
    JOBS_AVR.forEach(job => {
        let div = document.createElement('div');
        div.className = 'list-option';
        let chk = selectedAvr.includes(job) ? 'checked' : '';
        div.innerHTML = `<input type="checkbox" ${chk} value="${job}"><span>${job}</span>`;
        div.onclick = (e) => {
            if (e.target.type !== 'checkbox') {
                let i = div.querySelector('input');
                i.checked = !i.checked;
            }
        };
        container.appendChild(div);
    });
    document.getElementById('modal-avr').style.display = 'flex';
}

function saveAvrSelection() {
    selectedAvr = [];
    document.querySelectorAll('#avr-list-container input:checked').forEach(el => selectedAvr.push(el.value));
    
    let btn = document.getElementById('avr-main-btn');
    if (selectedAvr.length > 0) {
        btn.classList.add('checked');
        btn.innerHTML = `<span>⚡</span><span>АВР (${selectedAvr.length})</span>`;
    } else {
        btn.classList.remove('checked');
        btn.innerHTML = `<span>⚡</span><span>АВР</span>`;
    }
    updateSummary();
    document.getElementById('modal-avr').style.display = 'none';
}

// ==========================================================================
// 5. ТАБЛИЦА СКЛАДА И ФИЛЬТРЫ
// ==========================================================================

function openWarehouse() {
    document.getElementById('modal-stock').style.display = 'flex';
    renderStockTable();
}

function updateSelectOptions(selectId, items, placeholder, currentValue) {
    let select = document.getElementById(selectId);
    let html = `<option value="">${placeholder}</option>`;
    items.forEach(item => {
        html += `<option value="${item}" ${item === currentValue ? 'selected' : ''}>${item}</option>`;
    });
    select.innerHTML = html;
}

function renderStockTable() {
    let tbody = document.getElementById('stock-tbody');
    tbody.innerHTML = "";
    
    let search = document.getElementById('stock-search').value.toLowerCase();
    let filterCode = document.getElementById('filter-code').value;
    let filterType = document.getElementById('filter-type').value;
    let filterCheck = document.getElementById('filter-check').value;
    
    // Получение уникальных кодов для селекта фильтра
    let availableCodes = new Set();
    stockItems.forEach(i => {
        if (filterType === "" || i.type === filterType) availableCodes.add(i.code);
    });
    updateSelectOptions('filter-code', [...availableCodes].sort(), "Код", filterCode);
    
    // Получение уникальных типов для селекта фильтра
    let availableTypes = new Set();
    stockItems.forEach(i => {
        if (filterCode === "" || i.code === filterCode) availableTypes.add(i.type);
    });
    updateSelectOptions('filter-type', [...availableTypes].sort(), "Тип", filterType);
    
    // Фильтрация и рендер строк таблицы
    stockItems.forEach(item => {
        if (search && !String(item.sn).toLowerCase().includes(search)) return;
        if (filterCode && item.code !== filterCode) return;
        if (filterType && item.type !== filterType) return;
        if (filterCheck === "1" && !item.selected) return;
        if (filterCheck === "0" && item.selected) return;
        
        let tr = document.createElement('tr');
        if (item.selected) tr.classList.add('selected');
        
        tr.innerHTML = `
            <td style="font-size:14px; font-weight:600;">
                ${item.code}
                <div style="font-weight:normal; color:#888; font-size:12px;">${item.mustang}</div>
            </td>
            <td style="font-size:14px;">${item.type}</td>
            <td style="color:#d81b60; font-family:monospace; font-weight:bold; font-size:15px;">${item.sn}</td>
            <td class="check-cell"><input type="checkbox" ${item.selected ? 'checked' : ''}></td>
        `;
        
        tr.onclick = (e) => {
            if (e.target.tagName !== 'SELECT' && e.target.tagName !== 'OPTION') {
                item.selected = !item.selected;
                renderStockTable();
            }
        };
        tbody.appendChild(tr);
    });
}

function confirmStock() {
    let serials = stockItems.filter(i => i.selected).map(i => i.sn).join(', ');
    let input = document.getElementById('sn_mont');
    if (serials) {
        input.value = input.value ? input.value + ", " + serials : serials;
    }
    // Сброс выделения
    stockItems.forEach(i => i.selected = false);
    document.getElementById('modal-stock').style.display = 'none';
}

// ==========================================================================
// 6. СКАНЕР ШТРИХ-КОДОВ (ВСТРОЕННАЯ КАМЕРА)
// ==========================================================================

function startScanner(id) {
    currentScanId = id;
    document.getElementById('modal-scan').style.display = 'flex';

    // Вычисление динамической рамки сканирования (85% от экрана)
    const screenWidth = window.innerWidth;
    const boxWidth = Math.min(400, Math.floor(screenWidth * 0.85));
    const boxHeight = 150;

    if (!scanner) {
        scanner = new Html5Qrcode("reader", {
            experimentalFeatures: {
                useBarCodeDetectorIfSupported: true // Позволяет Chrome использовать аппаратное ускорение Android
            },
            formatsToSupport: [
                Html5QrcodeSupportedFormats.CODE_128,
                Html5QrcodeSupportedFormats.EAN_13,
                Html5QrcodeSupportedFormats.QR_CODE
            ],
            verbose: false
        });
    }

    const config = { facingMode: "environment" };
    const settings = {
        fps: 15,
        qrbox: { width: boxWidth, height: boxHeight },
        aspectRatio: 1.0,
        disableFlip: false
    };

    scanner.start(config, settings, (decodedText) => {
        // Успешный скан
        let input = document.getElementById(currentScanId);
        input.value += (input.value ? ", " : "") + decodedText;
        if (navigator.vibrate) navigator.vibrate(200);
        stopScanner();
    }).catch(err => {
        console.error("Ошибка камеры:", err);
        if (err && err.toString().includes("NotAllowedError")) {
            alert("❌ Доступ к камере заблокирован настройками вашего телефона.\n\nРазрешите камеру в настройках Telegram или используйте оранжевую кнопку «📂 Скан с фото» ниже.");
        } else {
            alert("Не удалось запустить камеру. Используйте загрузку фото.");
        }
    });
}

function stopScanner() {
    if (scanner && scanner.isScanning) {
        scanner.stop().catch(e => console.error("Ошибка при остановке камеры:", e));
    }
    document.getElementById('modal-scan').style.display = 'none';
}

// ==========================================================================
// 7. СКАНЕР ИЗ ФАЙЛОВ И ГАЛЕРЕИ (CROPPER.JS)
// ==========================================================================

function triggerFileScan() {
    document.getElementById('scan-file-input').click();
}

function handleFileScan(fileInput) {
    if (fileInput.files.length === 0) return;
    const file = fileInput.files[0];
    
    // Закрываем окно стандартного сканера
    document.getElementById('modal-scan').style.display = 'none';
    if (scanner && scanner.isScanning) {
        scanner.stop().catch(e => console.log(e));
    }

    // Уничтожаем старый кроппер (если он был)
    if (cropper) {
        cropper.destroy();
        cropper = null;
    }

    const reader = new FileReader();
    reader.onload = function(e) {
        const image = document.getElementById('image-to-crop');
        image.src = e.target.result;
        
        // Показываем модальное окно кроппера
        document.getElementById('modal-crop').style.display = 'flex';
        
        // Инициализация Cropper.js для выделения штрих-кода
        cropper = new Cropper(image, {
            viewMode: 1,
            dragMode: 'move',
            autoCropArea: 0.9,
            restore: false,
            guides: true,
            center: true,
            highlight: false,
            cropBoxMovable: true,
            cropBoxResizable: true,
            toggleDragModeOnDblclick: false
        });
    };
    reader.readAsDataURL(file);
    
    // Очищаем инпут
    fileInput.value = "";
}

function scanCroppedImage() {
    if (!cropper) return;

    // Конвертация выделенной области в Blob
    cropper.getCroppedCanvas().toBlob((blob) => {
        if (!blob) return;

        const croppedFile = new File([blob], "cropped.jpg", { type: "image/jpeg" });
        const scanBtn = document.querySelector('#modal-crop button[onclick="scanCroppedImage()"]');
        const originalText = scanBtn.innerText;
        scanBtn.innerText = "⏳ ...";

        if (!scanner) {
            scanner = new Html5Qrcode("reader", {
                experimentalFeatures: { useBarCodeDetectorIfSupported: true },
                formatsToSupport: [
                    Html5QrcodeSupportedFormats.CODE_128,
                    Html5QrcodeSupportedFormats.EAN_13,
                    Html5QrcodeSupportedFormats.QR_CODE
                ],
                verbose: false
            });
        }

        // Сканируем Blob картинки
        scanner.scanFileV2(croppedFile, true)
            .then(result => {
                let val = result.decodedText;
                let input = document.getElementById(currentScanId);
                input.value += (input.value ? ", " : "") + val;
                
                if (navigator.vibrate) navigator.vibrate(200);
                
                scanBtn.innerText = originalText;
                closeCropper();
                stopScanner();
            })
            .catch(err => {
                console.error("Ошибка сканирования файла:", err);
                scanBtn.innerText = originalText;
                alert("❌ Штрих-код не распознан. Попробуйте выделить область более точно или загрузить другую фотографию.");
            });
    }, 'image/jpeg', 0.9);
}

function closeCropper() {
    document.getElementById('modal-crop').style.display = 'none';
    if (cropper) {
        cropper.destroy();
        cropper = null;
    }
}

// ==========================================================================
// 8. ОТПРАВКА ДАННЫХ
// ==========================================================================

function sendData() {
    if (isSubmitting) return;

    const btn = document.getElementById('send-btn');
    const finalRegion = SERVER_REGION || "Unknown";
    const tkd = document.getElementById('tkd').value.trim();
    
    if (!tkd) {
        alert('Введите ТКД!');
        return;
    }

    isSubmitting = true;
    btn.disabled = true;
    btn.innerText = "СОХРАНЕНИЕ...";

    // Сбор всех выполненных работ
    let works = [];
    document.querySelectorAll('input[name="std_job"]:checked').forEach(el => works.push(el.value));
    works = works.concat(selectedAvr);
    
    const data = {
        tkd: tkd,
        address: document.getElementById('address-label').innerText.replace("🏠 ", "").replace("🔄 Поиск адреса...", ""),
        work_types: works.join(', '),
        comment: document.getElementById('comment').value.trim(),
        sn_dem: document.getElementById('sn_dem').value.trim(),
        sn_mont: document.getElementById('sn_mont').value.trim(),
        target_region: finalRegion
    };

    // Прямая передача данных боту для всех платформ (Android, iOS, ПК)
    const jsonData = JSON.stringify(data);
    tg.sendData(jsonData);
    tg.close();
}
