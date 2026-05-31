
    if (localStorage.getItem("pg-theme") === "light") {
        document.documentElement.style.background = "#f3f4f8";
    }


// ═══════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════
let currentEmployee = null;
let currentEmployeeFiles = null;
let currentDocFileId = null;
let currentDocFileName = null;
let activeTab = 'flagged'; // 'flagged' | 'all'

// ═══════════════════════════════════════════════════════════════
// EMPLOYEE SEARCH
// ═══════════════════════════════════════════════════════════════
async function searchEmployees() {
    const q = document.getElementById('adminSearchInput').value.trim();
    if (!q) return;

    // Trigger a background scan as requested so files are parsed
    try {
        fetch('/api/admin/trigger-scan', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({target_dir: './strict_drive'})
        }).catch(err => console.error("Background scan fetch error:", err));
    } catch (err) {
        console.error("Background scan trigger error:", err);
    }

    const resArea = document.getElementById('adminSearchResults');
    resArea.innerHTML = `
        <div style="text-align: center; padding: 30px; color: var(--text-muted);">
            <div class="loading-spinner"></div>
            <p style="margin-top: 12px; font-size: 13px;">Searching employees...</p>
        </div>
    `;

    try {
        const response = await fetch(`/api/admin/employees/search?q=${encodeURIComponent(q)}`);
        const data = await response.json();

        if (data.length === 0) {
            resArea.innerHTML = `
                <div class="db-empty-state">
                    <div class="empty-icon"><svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg></div>
                    <h3>No Employees Found</h3>
                    <p>No results matched "<strong>${escapeHtml(q)}</strong>". Try a different name, email, or employee ID.</p>
                </div>
            `;
            return;
        }

        let html = '';
        data.forEach(emp => {
            const initials = (emp.first_name?.[0] || '') + (emp.last_name?.[0] || '');
            html += `
                <div class="glass-card emp-result-card" id="emp-card-${emp.employee_id}" onclick="selectEmployee(${JSON.stringify(emp).replace(/"/g, '&quot;')})">
                    <div class="emp-info">
                        <div class="emp-avatar">${initials}</div>
                        <div class="emp-details">
                            <div class="emp-name">${escapeHtml(emp.first_name)} ${escapeHtml(emp.last_name)}</div>
                            <div class="emp-meta">${escapeHtml(emp.email)} · ID: ${escapeHtml(emp.employee_id)}</div>
                        </div>
                    </div>
                    <span class="emp-dept-badge">${escapeHtml(emp.department)}</span>
                </div>
            `;
        });
        resArea.innerHTML = html;

    } catch (err) {
        resArea.innerHTML = `
            <div class="db-empty-state">
                <div class="empty-icon"><svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#e74c3c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg></div>
                <h3>Search Failed</h3>
                <p>${escapeHtml(err.message)}</p>
            </div>
        `;
    }
}

// ═══════════════════════════════════════════════════════════════
// SELECT EMPLOYEE → LOAD FILES
// ═══════════════════════════════════════════════════════════════
async function selectEmployee(emp) {
    currentEmployee = emp;

    // Highlight selected card
    document.querySelectorAll('.emp-result-card').forEach(c => c.classList.remove('selected'));
    const card = document.getElementById(`emp-card-${emp.employee_id}`);
    if (card) card.classList.add('selected');

    const container = document.getElementById('empDetailContainer');
    container.innerHTML = `
        <div class="glass-card emp-detail-panel" style="padding: 0; overflow: hidden;">
            <div style="text-align: center; padding: 50px; color: var(--text-muted);">
                <div class="loading-spinner"></div>
                <p style="margin-top: 12px; font-size: 13px;">Loading ${escapeHtml(emp.first_name)}'s files...</p>
            </div>
        </div>
    `;

    // Smooth scroll to detail panel
    setTimeout(() => container.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);

    try {
        const response = await fetch(`/api/user-details/${encodeURIComponent(emp.employee_id)}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        currentEmployeeFiles = data;
        activeTab = 'flagged';
        renderEmployeeDetail(emp, data);
    } catch (err) {
        container.innerHTML = `
            <div class="glass-card emp-detail-panel" style="padding: 40px; text-align: center;">
                <div class="db-empty-state">
                    <div class="empty-icon"><svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#e74c3c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg></div>
                    <h3>Failed to Load Files</h3>
                    <p>${escapeHtml(err.message)}</p>
                </div>
            </div>
        `;
    }
}

// ═══════════════════════════════════════════════════════════════
// RENDER EMPLOYEE DETAIL PANEL
// ═══════════════════════════════════════════════════════════════
function renderEmployeeDetail(emp, data) {
    const files = data.files || [];
    const flaggedFiles = files.filter(f => f.findings && f.findings.length > 0);
    const cleanFiles = files.filter(f => !f.findings || f.findings.length === 0);
    const totalFindings = flaggedFiles.reduce((sum, f) => sum + f.findings.length, 0);

    const initials = (emp.first_name?.[0] || '') + (emp.last_name?.[0] || '');

    const container = document.getElementById('empDetailContainer');

    let html = `
        <div class="glass-card emp-detail-panel" style="padding: 0; overflow: hidden;">
            <!-- Header -->
            <div class="emp-detail-header">
                <div class="emp-detail-avatar">${initials}</div>
                <div class="emp-detail-info">
                    <h2>${escapeHtml(emp.first_name)} ${escapeHtml(emp.last_name)}</h2>
                    <p>${escapeHtml(emp.email)} · ${escapeHtml(emp.department)} · ${escapeHtml(emp.location)}</p>
                </div>
                <div class="emp-detail-stats">
                    <div class="emp-stat-box">
                        <div class="stat-value">${files.length}</div>
                        <div class="stat-label">Total Files</div>
                    </div>
                    <div class="emp-stat-box">
                        <div class="stat-value danger">${flaggedFiles.length}</div>
                        <div class="stat-label">Flagged</div>
                    </div>
                    <div class="emp-stat-box">
                        <div class="stat-value warning">${totalFindings}</div>
                        <div class="stat-label">Findings</div>
                    </div>
                    <div class="emp-stat-box">
                        <div class="stat-value success">${cleanFiles.length}</div>
                        <div class="stat-label">Clean</div>
                    </div>
                </div>
            </div>

            <!-- Tabs -->
            <div class="detail-tabs">
                <button class="detail-tab ${activeTab === 'flagged' ? 'active' : ''}" onclick="switchTab('flagged')">
                    Flagged Documents (${flaggedFiles.length})
                </button>
                <button class="detail-tab ${activeTab === 'all' ? 'active' : ''}" onclick="switchTab('all')">
                    All Files (${files.length})
                </button>
            </div>

            <!-- File List -->
            <div class="files-list" id="filesList">
    `;

    const displayFiles = activeTab === 'flagged' ? flaggedFiles : files;

    if (displayFiles.length === 0) {
        html += `
            <div class="db-empty-state">
                <div class="empty-icon">${activeTab === 'flagged' ? '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#2ecc71" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>' : '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>'}</div>
                <h3>${activeTab === 'flagged' ? 'No Flagged Documents' : 'No Files Found'}</h3>
                <p>${activeTab === 'flagged' ? 'This employee has no documents with GDPR violations. Great job!' : 'No documents found in this employee\'s repository.'}</p>
            </div>
        `;
    } else {
        displayFiles.forEach(file => {
            const hasFindings = file.findings && file.findings.length > 0;
            const fileIcon = getFileIcon(file.file_name);
            const sizeKB = (file.size_bytes / 1024).toFixed(1);

            html += `
                <div class="file-row" onclick="toggleFindings('${file.file_id}')">
                    <div class="file-icon">${fileIcon}</div>
                    <div class="file-info">
                        <div class="file-name">${escapeHtml(file.file_name)}</div>
                        <div class="file-path">${escapeHtml(file.file_path)} · ${sizeKB} KB</div>
                    </div>
                    <div class="file-badges">
                        ${hasFindings ? `
                            <span class="badge violation">GDPR Violation</span>
                            <span class="badge findings-count">${file.findings.length} finding${file.findings.length > 1 ? 's' : ''}</span>
                        ` : `
                            <span class="badge clean">Clean</span>
                        `}
                    </div>
                    <div class="file-actions" onclick="event.stopPropagation()">
                        <button class="file-action-btn" onclick="openDocReader('${escapeHtml(file.file_name)}', '${file.file_id}', ${JSON.stringify(file.findings || []).replace(/"/g, '&quot;')})">
                            Read
                        </button>
                        ${hasFindings ? `
                            <button class="file-action-btn primary" onclick="openJustifyModalDirect('${file.file_id}', '${escapeHtml(file.file_name)}')">
                                Retain
                            </button>
                        ` : ''}
                    </div>
                </div>
                <div id="findings-${file.file_id}" style="display: none;">
            `;

            if (hasFindings) {
                file.findings.forEach(finding => {
                    html += `
                        <div class="finding-detail">
                            <div class="finding-category">${getCategoryIcon(finding.category)} ${escapeHtml(finding.category || 'Uncategorized')}</div>
                            <div class="finding-snippet">${escapeHtml(finding.flagged_snippet || 'N/A')}</div>
                            <div class="finding-reasoning">${escapeHtml(finding.reasoning || 'No reasoning provided.')}</div>
                        </div>
                    `;
                });
            }

            html += `</div>`; // close findings container
        });
    }

    html += `
            </div>
        </div>
    `;

    container.innerHTML = html;
}

function switchTab(tab) {
    activeTab = tab;
    if (currentEmployee && currentEmployeeFiles) {
        renderEmployeeDetail(currentEmployee, currentEmployeeFiles);
    }
}

function toggleFindings(fileId) {
    const el = document.getElementById(`findings-${fileId}`);
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
}

// ═══════════════════════════════════════════════════════════════
// DOCUMENT READER MODAL
// ═══════════════════════════════════════════════════════════════
function openDocReader(fileName, fileId, findings) {
    currentDocFileId = fileId;
    currentDocFileName = fileName;

    document.getElementById('docReaderTitle').textContent = fileName;

    // Simulated document content
    document.getElementById('docReaderContent').textContent =
        `[RESTRICTED DOCUMENT CONTENT]\n\n` +
        `Filename: ${fileName}\n` +
        `Classification: Internal / Confidential\n` +
        `Accessed by: Admin (${new Date().toLocaleString()})\n` +
        `Employee: ${currentEmployee ? currentEmployee.first_name + ' ' + currentEmployee.last_name : 'Unknown'}\n\n` +
        `─────────────────────────────────────\n\n` +
        `This is a secure preview of the document contents\nfor administrative GDPR review purposes.\n\n` +
        `All access is logged and auditable per\ncompany data governance policy.\n\n` +
        `─────────────────────────────────────\n\n` +
        `... Extracted text content for analysis ...\n` +
        `Ensure compliance with internal data handling\npolicies before making retention decisions.`;

    // Render findings in the modal if present
    const findingsContainer = document.getElementById('docReaderFindings');
    if (findings && findings.length > 0) {
        let fHtml = `
            <div class="doc-findings-section">
                <h3>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                    ${findings.length} Flagged Finding${findings.length > 1 ? 's' : ''} in This Document
                </h3>
        `;
        findings.forEach(f => {
            fHtml += `
                <div class="finding-detail" style="margin-left: 0;">
                    <div class="finding-category">${getCategoryIcon(f.category)} ${escapeHtml(f.category || 'Uncategorized')}</div>
                    <div class="finding-snippet">${escapeHtml(f.flagged_snippet || 'N/A')}</div>
                    <div class="finding-reasoning">${escapeHtml(f.reasoning || 'No reasoning provided.')}</div>
                </div>
            `;
        });
        fHtml += `</div>`;
        findingsContainer.innerHTML = fHtml;
        document.getElementById('docRetainBtn').style.display = '';
    } else {
        findingsContainer.innerHTML = '';
        document.getElementById('docRetainBtn').style.display = 'none';
    }

    document.getElementById('docReaderModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeDocReader() {
    document.getElementById('docReaderModal').classList.remove('active');
    document.body.style.overflow = '';
}

// ═══════════════════════════════════════════════════════════════
// BUSINESS JUSTIFICATION MODAL
// ═══════════════════════════════════════════════════════════════
function openJustifyModal() {
    // Close the doc reader first
    closeDocReader();
    _openJustify();
}

function openJustifyModalDirect(fileId, fileName) {
    currentDocFileId = fileId;
    currentDocFileName = fileName;
    _openJustify();
}

function _openJustify() {
    // Reset form
    document.getElementById('justifyReason').value = '';
    document.getElementById('justifyProject').value = '';
    document.getElementById('justifyNotes').value = '';

    document.getElementById('justifyModal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeJustifyModal() {
    document.getElementById('justifyModal').classList.remove('active');
    document.body.style.overflow = '';
}

async function submitJustification() {
    const reason = document.getElementById('justifyReason').value;
    const project = document.getElementById('justifyProject').value.trim();
    const notes = document.getElementById('justifyNotes').value.trim();

    if (!reason) {
        document.getElementById('justifyReason').style.borderColor = '#ff4d6a';
        document.getElementById('justifyReason').focus();
        return;
    }
    if (!project) {
        document.getElementById('justifyProject').style.borderColor = '#ff4d6a';
        document.getElementById('justifyProject').focus();
        return;
    }

    // Reset border colors
    document.getElementById('justifyReason').style.borderColor = '';
    document.getElementById('justifyProject').style.borderColor = '';

    try {
        const response = await fetch(`/api/admin/retain-document/${encodeURIComponent(currentDocFileId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                reason: reason,
                project_name: project,
                notes: notes,
                admin_email: '{{ user.email }}'
            })
        });

        const result = await response.json();

        closeJustifyModal();
        showToast(`✓ "${currentDocFileName}" marked as business-critical — ${project}`);

    } catch (err) {
        alert('Failed to submit justification: ' + err.message);
    }
}

// ═══════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function getFileIcon(name) {
    if (!name) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';
    const ext = name.split('.').pop().toLowerCase();
    if (['pdf'].includes(ext)) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#e74c3c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M16 13H8"></path><path d="M16 17H8"></path><path d="M10 9H8"></path></svg>';
    if (['doc', 'docx'].includes(ext)) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#3498db" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M16 13H8"></path><path d="M16 17H8"></path><path d="M10 9H8"></path></svg>';
    if (['xls', 'xlsx', 'csv'].includes(ext)) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#2ecc71" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><path d="M3 9h18"></path><path d="M9 21V9"></path></svg>';
    if (['jpg', 'jpeg', 'png', 'gif', 'svg'].includes(ext)) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#9b59b6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>';
    if (['txt', 'log'].includes(ext)) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#95a5a6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>';
    if (['zip', 'rar', '7z'].includes(ext)) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#e67e22" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="16.5" y1="9.4" x2="7.5" y2="4.21"></line><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>';
    return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';
}

function getCategoryIcon(category) {
    category = (category || '').toLowerCase();
    if (category.includes('passport')) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#34495e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2" ry="2"></rect><line x1="12" y1="2" x2="12" y2="22"></line></svg>';
    if (category.includes('financial') || category.includes('bank') || category.includes('iban') || category.includes('salary') || category.includes('tax') || category.includes('credit')) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#27ae60" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 22 7 12 2"></polygon><polyline points="2 17 2 22 22 22 22 17"></polyline><line x1="6" y1="12" x2="6" y2="17"></line><line x1="10" y1="12" x2="10" y2="17"></line><line x1="14" y1="12" x2="14" y2="17"></line><line x1="18" y1="12" x2="18" y2="17"></line></svg>';
    if (category.includes('contact') || category.includes('phone') || category.includes('email') || category.includes('address')) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#8e44ad" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>';
    if (category.includes('id') || category.includes('identity')) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#d35400" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2" ry="2"></rect><path d="M7 15V9h4v6H7z"></path><path d="M15 15V9"></path><path d="M15 12h2"></path></svg>';
    if (category.includes('medical')) return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="#c0392b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="12" y1="8" x2="12" y2="16"></line><line x1="8" y1="12" x2="16" y2="12"></line></svg>';
    return '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';
}

function showToast(message) {
    // Remove any existing toast
    const existing = document.querySelector('.success-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = 'success-toast';
    toast.innerHTML = message;
    document.body.appendChild(toast);
    setTimeout(() => { if (toast.parentNode) toast.remove(); }, 3200);
}

// Close modals on overlay click
document.getElementById('docReaderModal').addEventListener('click', function(e) {
    if (e.target === this) closeDocReader();
});
document.getElementById('justifyModal').addEventListener('click', function(e) {
    if (e.target === this) closeJustifyModal();
});

// Close modals on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        if (document.getElementById('justifyModal').classList.contains('active')) {
            closeJustifyModal();
        } else if (document.getElementById('docReaderModal').classList.contains('active')) {
            closeDocReader();
        }
    }
});


