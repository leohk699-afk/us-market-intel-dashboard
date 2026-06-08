const statusText = {
  green: '绿色：可进攻',
  yellow: '黄色：谨慎',
  red: '红色：防守'
};

function fmt(x, unit='') {
  if (x === null || x === undefined || Number.isNaN(x)) return '--';
  const abs = Math.abs(Number(x));
  let v;
  if (abs >= 1000000) v = Number(x).toLocaleString(undefined, { maximumFractionDigits: 0 });
  else if (abs >= 1000) v = Number(x).toLocaleString(undefined, { maximumFractionDigits: 1 });
  else if (abs >= 100) v = Number(x).toFixed(1);
  else v = Number(x).toFixed(2);
  return unit ? `${v} ${unit}` : v;
}

function fmtPct(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return '--';
  const sign = Number(x) > 0 ? '+' : '';
  return `${sign}${Number(x).toFixed(2)}%`;
}

function escapeHtml(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function loadDashboard() {
  const resp = await fetch('data/latest.json', { cache: 'no-store' });
  if (!resp.ok) throw new Error('Cannot load data/latest.json');
  const data = await resp.json();

  document.getElementById('generatedAt').textContent = `最后更新：${new Date(data.generated_at).toLocaleString()} · ${data.version || 'v1'}`;
  const overallStatus = document.getElementById('overallStatus');
  overallStatus.textContent = data.overall.status_label || statusText[data.overall.status] || data.overall.status;
  overallStatus.className = `status-pill ${data.overall.status}`;
  document.getElementById('overallScore').textContent = data.overall.score;
  document.getElementById('summary').textContent = data.overall.summary;

  renderTopRisks(data);
  renderModules(data);
  renderWatchlist(data);
  renderIndicators(data);
}

function renderTopRisks(data) {
  const topRisks = document.getElementById('topRisks');
  topRisks.innerHTML = '';
  if (!data.top_risks || data.top_risks.length === 0) {
    topRisks.innerHTML = '<div class="risk-item"><div><div class="name">暂无突出高风险指标</div><div class="meta">继续观察利率、通胀和波动率。</div></div></div>';
    return;
  }
  data.top_risks.forEach(r => {
    const div = document.createElement('div');
    div.className = 'risk-item';
    div.innerHTML = `
      <div>
        <div class="name">${escapeHtml(r.label)}</div>
        <div class="meta">${escapeHtml(r.module)} · ${fmt(r.value, r.unit)}</div>
      </div>
      <div class="risk-score">${r.score}</div>
    `;
    topRisks.appendChild(div);
  });
}

function renderModules(data) {
  const modules = document.getElementById('modules');
  modules.innerHTML = '';
  data.modules.forEach(m => {
    const div = document.createElement('div');
    div.className = 'module-card';
    div.innerHTML = `
      <div class="module-head">
        <div class="module-title">${escapeHtml(m.name)}</div>
        <div class="module-score">${m.score}</div>
      </div>
      <div class="bar"><div class="bar-inner ${m.status}" style="width:${Math.min(100, Math.max(0, m.score))}%"></div></div>
      <div class="module-meta">权重 ${m.weight}% · ${statusText[m.status] || m.status} · ${m.indicators.length} 个指标</div>
    `;
    modules.appendChild(div);
  });
}

function renderWatchlist(data) {
  const groupsEl = document.getElementById('watchlistGroups');
  const providerEl = document.getElementById('watchlistProvider');
  groupsEl.innerHTML = '';

  if (!data.watchlist || !data.watchlist.groups) {
    providerEl.textContent = '未检测到个股观察池数据';
    groupsEl.innerHTML = '<div class="card"><p class="muted">当前 latest.json 暂无 watchlist 字段。请确认已上传 V2 代码并重新运行 Actions。</p></div>';
    return;
  }

  providerEl.textContent = `${data.watchlist.provider || 'Market data'} · ${data.watchlist.note || ''}`;

  data.watchlist.groups.forEach(group => {
    const wrap = document.createElement('div');
    wrap.className = 'watchlist-card';
    const rows = group.items.map(item => `
      <tr>
        <td><strong>${escapeHtml(item.symbol)}</strong><div class="subtext">${escapeHtml(item.name)}</div></td>
        <td>${escapeHtml(item.category_label)}</td>
        <td>${fmt(item.price, 'USD')}<div class="subtext">${item.date || '--'}</div></td>
        <td class="${Number(item.change_1_pct) >= 0 ? 'pos' : 'neg'}">${fmtPct(item.change_1_pct)}</td>
        <td class="${Number(item.change_5_pct) >= 0 ? 'pos' : 'neg'}">${fmtPct(item.change_5_pct)}</td>
        <td class="${Number(item.change_20_pct) >= 0 ? 'pos' : 'neg'}">${fmtPct(item.change_20_pct)}</td>
        <td>${item.risk_score}</td>
        <td><span class="badge ${item.status}">${statusText[item.status] || item.status}</span></td>
        <td class="hint-cell">${escapeHtml(item.hint)}</td>
      </tr>
    `).join('');
    wrap.innerHTML = `
      <div class="watchlist-head">
        <div>
          <h3>${escapeHtml(group.name)}</h3>
          <p>${escapeHtml(group.description)}</p>
        </div>
      </div>
      <div class="table-wrap inner-table">
        <table>
          <thead>
            <tr>
              <th>标的</th>
              <th>类型</th>
              <th>价格</th>
              <th>1日</th>
              <th>5日</th>
              <th>20日</th>
              <th>风险分</th>
              <th>状态</th>
              <th>系统提示</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
    groupsEl.appendChild(wrap);
  });
}

function renderIndicators(data) {
  const tbody = document.getElementById('indicatorRows');
  tbody.innerHTML = '';
  data.modules.forEach(m => {
    m.indicators.forEach(ind => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(m.name)}</td>
        <td>${escapeHtml(ind.label)}</td>
        <td>${fmt(ind.value, ind.unit)}</td>
        <td>${ind.date || '--'}</td>
        <td>${fmt(ind.change_1, ind.unit)}</td>
        <td>${fmt(ind.change_5, ind.unit)}</td>
        <td>${ind.risk_score}</td>
        <td><span class="badge ${ind.status}">${statusText[ind.status] || ind.status}</span></td>
      `;
      tbody.appendChild(tr);
    });
  });
}

loadDashboard().catch(err => {
  document.getElementById('summary').textContent = `加载失败：${err.message}`;
});
