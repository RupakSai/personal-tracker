(function () {
    const state = {
        unlocked: document.body.dataset.unlocked === 'true',
        pin: '',
        shownMonth: startOfMonth(new Date()),
        selectedDate: null,
        pendingEstimate: null,
    };

    const lockScreen = document.getElementById('lockScreen');
    const appShell = document.getElementById('appShell');
    const pinInput = document.getElementById('pinInput');
    const pinPad = document.getElementById('pinPad');
    const pinError = document.getElementById('pinError');
    const monthTitle = document.getElementById('monthTitle');
    const calendarGrid = document.getElementById('calendarGrid');
    const dayPanel = document.getElementById('dayPanel');
    const selectedDateTitle = document.getElementById('selectedDateTitle');
    const caloriesTotal = document.getElementById('caloriesTotal');
    const proteinTotal = document.getElementById('proteinTotal');
    const fatTotal = document.getElementById('fatTotal');
    const caloriesMeter = document.getElementById('caloriesMeter');
    const proteinMeter = document.getElementById('proteinMeter');
    const fatMeter = document.getElementById('fatMeter');
    const statusList = document.getElementById('statusList');
    const entryList = document.getElementById('entryList');
    const entryCount = document.getElementById('entryCount');
    const sheetBackdrop = document.getElementById('sheetBackdrop');
    const entrySheet = document.getElementById('entrySheet');
    const manualForm = document.getElementById('manualForm');
    const aiForm = document.getElementById('aiForm');
    const aiResult = document.getElementById('aiResult');
    const sheetError = document.getElementById('sheetError');
    const entryModeChoices = document.getElementById('entryModeChoices');
    const askAiButton = document.getElementById('askAiButton');
    const toast = document.getElementById('toast');

    boot();

    function boot() {
        setUnlocked(state.unlocked);
        bindEvents();
        renderCalendar();
        if (state.unlocked) {
            selectDate(toDateKey(new Date()));
        }
    }

    function bindEvents() {
        pinInput.addEventListener('focus', function () {
            pinInput.blur();
        });

        pinPad.addEventListener('click', function (event) {
            const button = event.target.closest('button[data-key]');
            if (!button) return;
            handlePinKey(button.dataset.key);
        });

        document.getElementById('logoutButton').addEventListener('click', async function () {
            await api('/api/logout/', { method: 'POST' });
            state.pin = '';
            state.unlocked = false;
            setUnlocked(false);
        });

        document.getElementById('previousMonth').addEventListener('click', function () {
            state.shownMonth = new Date(state.shownMonth.getFullYear(), state.shownMonth.getMonth() - 1, 1);
            renderCalendar();
        });

        document.getElementById('nextMonth').addEventListener('click', function () {
            state.shownMonth = new Date(state.shownMonth.getFullYear(), state.shownMonth.getMonth() + 1, 1);
            renderCalendar();
        });

        document.querySelectorAll('[data-shortcut]').forEach(function (button) {
            button.addEventListener('click', function () {
                const date = new Date();
                if (button.dataset.shortcut === 'yesterday') {
                    date.setDate(date.getDate() - 1);
                }
                if (button.dataset.shortcut === 'day-before') {
                    date.setDate(date.getDate() - 2);
                }
                state.shownMonth = startOfMonth(date);
                renderCalendar().then(function () {
                    selectDate(toDateKey(date));
                });
            });
        });

        document.getElementById('openAddSheet').addEventListener('click', openSheet);
        document.getElementById('closeSheet').addEventListener('click', closeSheet);
        sheetBackdrop.addEventListener('click', closeSheet);

        entryModeChoices.addEventListener('click', function (event) {
            const button = event.target.closest('button[data-mode]');
            if (!button) return;
            setEntryMode(button.dataset.mode);
        });

        manualForm.addEventListener('submit', async function (event) {
            event.preventDefault();
            clearSheetError();
            const data = Object.fromEntries(new FormData(manualForm).entries());
            try {
                await saveEntry({
                    source: 'manual',
                    calories: data.calories,
                    protein_g: data.protein_g,
                    fat_g: data.fat_g,
                });
                closeSheet();
                showToast('Saved. Nice tracking.');
            } catch (error) {
                showSheetError(error.message);
            }
        });

        aiForm.addEventListener('submit', async function (event) {
            event.preventDefault();
            clearSheetError();
            state.pendingEstimate = null;
            aiResult.hidden = true;
            askAiButton.disabled = true;
            askAiButton.innerHTML = '<span class="button-spinner"></span> Estimating';

            try {
                const data = Object.fromEntries(new FormData(aiForm).entries());
                const response = await api('/api/ai-estimate/', {
                    method: 'POST',
                    body: JSON.stringify(data),
                });
                state.pendingEstimate = response.estimate;
                renderAiResult(response.estimate);
            } catch (error) {
                showSheetError(error.message);
            } finally {
                askAiButton.disabled = false;
                askAiButton.textContent = 'Ask AI';
            }
        });

        aiResult.addEventListener('click', async function (event) {
            const action = event.target.closest('[data-ai-action]');
            if (!action || !state.pendingEstimate) return;

            if (action.dataset.aiAction === 'approve') {
                try {
                    await saveEntry({
                        source: 'ai',
                        calories: state.pendingEstimate.calories,
                        protein_g: state.pendingEstimate.protein_g,
                        fat_g: state.pendingEstimate.fat_g,
                    });
                    clearAiFlow();
                    closeSheet();
                    showToast('Approved and added.');
                } catch (error) {
                    showSheetError(error.message);
                }
            }

            if (action.dataset.aiAction === 'discard') {
                clearAiFlow();
                showToast('Estimate discarded.');
            }
        });

        entryList.addEventListener('click', async function (event) {
            const button = event.target.closest('[data-delete-entry]');
            if (!button) return;
            await api(`/api/entries/${button.dataset.deleteEntry}/delete/`, { method: 'POST' });
            await selectDate(state.selectedDate);
            await renderCalendar();
        });
    }

    function handlePinKey(key) {
        pinError.textContent = '';
        if (key === 'clear') {
            state.pin = '';
        } else if (key === 'back') {
            state.pin = state.pin.slice(0, -1);
        } else if (state.pin.length < 4) {
            state.pin += key;
        }
        pinInput.value = state.pin;

        if (state.pin.length === 4) {
            unlock();
        }
    }

    async function unlock() {
        try {
            await api('/api/unlock/', {
                method: 'POST',
                body: JSON.stringify({ pin: state.pin }),
            });
            state.unlocked = true;
            setUnlocked(true);
            await renderCalendar();
            await selectDate(toDateKey(new Date()));
        } catch (error) {
            pinError.textContent = error.message;
            state.pin = '';
            pinInput.value = '';
        }
    }

    function setUnlocked(isUnlocked) {
        lockScreen.hidden = isUnlocked;
        appShell.hidden = !isUnlocked;
    }

    async function renderCalendar() {
        const year = state.shownMonth.getFullYear();
        const month = state.shownMonth.getMonth();
        const monthLabel = state.shownMonth.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        monthTitle.textContent = monthLabel;
        calendarGrid.innerHTML = '';

        const summary = state.unlocked ? await loadMonthSummary(year, month + 1) : {};
        const firstWeekday = mondayBasedWeekday(new Date(year, month, 1));
        const daysInMonth = new Date(year, month + 1, 0).getDate();

        for (let i = 0; i < firstWeekday; i += 1) {
            const blank = document.createElement('span');
            blank.className = 'calendar-blank';
            calendarGrid.appendChild(blank);
        }

        for (let day = 1; day <= daysInMonth; day += 1) {
            const date = new Date(year, month, day);
            const key = toDateKey(date);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'calendar-day';
            button.textContent = String(day);
            button.dataset.date = key;
            if (key === toDateKey(new Date())) button.classList.add('today');
            if (key === state.selectedDate) button.classList.add('selected');
            if (summary[key]) {
                button.classList.add('has-data');
                const totals = summary[key];
                if (totals.calories > totals.targets.calories_max || totals.fat_g > totals.targets.fat_max_g) {
                    button.classList.add('attention');
                }
            }
            button.addEventListener('click', function () {
                selectDate(key);
            });
            calendarGrid.appendChild(button);
        }
    }

    async function loadMonthSummary(year, month) {
        try {
            const response = await api(`/api/month/?year=${year}&month=${month}`);
            return response.days || {};
        } catch (_error) {
            return {};
        }
    }

    async function selectDate(dateKey) {
        state.selectedDate = dateKey;
        document.querySelectorAll('.calendar-day').forEach(function (button) {
            button.classList.toggle('selected', button.dataset.date === dateKey);
        });

        dayPanel.hidden = false;
        selectedDateTitle.textContent = prettyDate(dateKey);
        try {
            const response = await api(`/api/day/${dateKey}/`);
            renderTotals(response.totals);
            renderEntries(response.entries);
        } catch (error) {
            renderDayError(error.message);
        }
    }

    function renderDayError(message) {
        renderTotals({
            calories: 0,
            protein_g: 0,
            fat_g: 0,
            targets: {
                calories_max: 1500,
                protein_min_g: 70,
                fat_max_g: 50,
            },
            messages: [
                {
                    kind: 'bad',
                    text: message || 'Could not load this day.',
                },
            ],
        });
        entryCount.textContent = '0 items';
        entryList.innerHTML = '<div class="empty-state">Day details could not load.</div>';
    }

    function renderTotals(totals) {
        caloriesTotal.textContent = String(totals.calories);
        proteinTotal.textContent = formatNumber(totals.protein_g);
        fatTotal.textContent = formatNumber(totals.fat_g);

        caloriesMeter.style.width = `${Math.min(100, (totals.calories / totals.targets.calories_max) * 100)}%`;
        proteinMeter.style.width = `${Math.min(100, (totals.protein_g / totals.targets.protein_min_g) * 100)}%`;
        fatMeter.style.width = `${Math.min(100, (totals.fat_g / totals.targets.fat_max_g) * 100)}%`;

        statusList.innerHTML = totals.messages.map(function (message) {
            return `<div class="status-pill ${message.kind}">${escapeHtml(message.text)}</div>`;
        }).join('');
    }

    function renderEntries(entries) {
        entryCount.textContent = `${entries.length} ${entries.length === 1 ? 'item' : 'items'}`;
        if (!entries.length) {
            entryList.innerHTML = '<div class="empty-state">No entries yet for this day.</div>';
            return;
        }

        entryList.innerHTML = entries.map(function (entry) {
            return `
                <div class="entry-row">
                    <div>
                        <strong>${entry.calories} kcal</strong>
                        <span>${escapeHtml(entry.source_label)} · ${formatNumber(entry.protein_g)} g protein · ${formatNumber(entry.fat_g)} g fat</span>
                    </div>
                    <button class="delete-entry" type="button" data-delete-entry="${entry.id}" aria-label="Delete entry">x</button>
                </div>
            `;
        }).join('');
    }

    async function saveEntry(payload) {
        const response = await api(`/api/day/${state.selectedDate}/entries/`, {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        renderTotals(response.totals);
        await selectDate(state.selectedDate);
        await renderCalendar();
    }

    function openSheet() {
        sheetBackdrop.hidden = false;
        entrySheet.hidden = false;
        setEntryMode(null);
        clearSheetError();
        clearAiFlow();
    }

    function closeSheet() {
        sheetBackdrop.hidden = true;
        entrySheet.hidden = true;
        manualForm.reset();
        aiForm.reset();
        setEntryMode(null);
        clearSheetError();
        clearAiFlow();
    }

    function setEntryMode(mode) {
        entryModeChoices.querySelectorAll('button').forEach(function (button) {
            button.classList.toggle('active', button.dataset.mode === mode);
        });
        manualForm.hidden = mode !== 'manual';
        aiForm.hidden = mode !== 'ai';
        clearSheetError();
        if (mode !== 'ai') {
            clearAiFlow();
        }
    }

    function renderAiResult(estimate) {
        aiResult.hidden = false;
        aiResult.innerHTML = `
            <h3>AI estimate</h3>
            <div class="estimate-grid">
                <span><b>${estimate.calories}</b> kcal</span>
                <span><b>${formatNumber(estimate.protein_g)}</b> protein g</span>
                <span><b>${formatNumber(estimate.fat_g)}</b> fat g</span>
            </div>
            <p>${escapeHtml(estimate.explanation || 'Estimate ready for approval.')}</p>
            <div class="approval-row">
                <button class="approve-button" type="button" data-ai-action="approve" aria-label="Approve estimate">
                    <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>
                    <span>Approve</span>
                </button>
                <button class="discard-button" type="button" data-ai-action="discard" aria-label="Disapprove estimate">
                    <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>
                    <span>Disapprove</span>
                </button>
            </div>
        `;
    }

    function clearAiFlow() {
        state.pendingEstimate = null;
        aiResult.hidden = true;
        aiResult.innerHTML = '';
        aiForm.reset();
    }

    function clearSheetError() {
        sheetError.textContent = '';
    }

    function showSheetError(message) {
        sheetError.textContent = message;
    }

    function showToast(message) {
        toast.textContent = message;
        toast.hidden = false;
        toast.classList.remove('show');
        requestAnimationFrame(function () {
            toast.classList.add('show');
        });

        window.clearTimeout(showToast.timer);
        showToast.timer = window.setTimeout(function () {
            toast.classList.remove('show');
            window.setTimeout(function () {
                toast.hidden = true;
            }, 220);
        }, 2200);
    }

    async function api(url, options) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            ...(options || {}),
        });

        const data = await response.json().catch(function () {
            return {};
        });

        if (!response.ok) {
            throw new Error(data.error || 'Something went wrong.');
        }
        return data;
    }

    function getCookie(name) {
        return document.cookie
            .split(';')
            .map(function (cookie) {
                return cookie.trim();
            })
            .filter(function (cookie) {
                return cookie.startsWith(`${name}=`);
            })
            .map(function (cookie) {
                return decodeURIComponent(cookie.slice(name.length + 1));
            })[0] || '';
    }

    function startOfMonth(date) {
        return new Date(date.getFullYear(), date.getMonth(), 1);
    }

    function mondayBasedWeekday(date) {
        return (date.getDay() + 6) % 7;
    }

    function toDateKey(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function prettyDate(dateKey) {
        const parts = dateKey.split('-').map(Number);
        return new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString(undefined, {
            weekday: 'long',
            month: 'short',
            day: 'numeric',
            year: 'numeric',
        });
    }

    function formatNumber(value) {
        const number = Number(value || 0);
        return Number.isInteger(number) ? String(number) : number.toFixed(1);
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
})();
