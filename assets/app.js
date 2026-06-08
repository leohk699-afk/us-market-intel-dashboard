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

async function loadDashboard() {
  const resp = await fetch('data/latest.json', { cache: 'no-store' });
  if (!resp.ok) throw new Error('Cannot load data/latest.json');
  const data = await resp.json();

  document.getElementById('generatedAt').textContent = `最后更新：${new Date(data.generated_at).toLocaleString()}`;
  const overallStatus = document.getElementById('overallStatus');
  overallStatus.textContent = data.overall.status_label || statusText[data.overall.status] || data.overall.status;
  overallStatus.className = `status-pill ${data.overall.status}`;
  document.getElementById('overallScore').textContent = data.overall.score;
  document.getElementById('summary').textContent = data.overall.summary;

  const topRisks = document.getElementById('topRisks');
  topRisks.innerHTML = '';
  if (!data.top_risks || data.top_risks.length === 0) {
    topRisks.innerHTML = '<div class="risk-item"><div><div class="name">暂无突出高风险指标</div><div class="meta">继续观察利率、通胀和波动率。</div></div></div>';
  } else {
    data.top_risks.forEach(r => {
      const div = document.createElement('div');
      div.className = 'risk-item';
      div.innerHTML = `
        <div>
          <div class="name">${r.label}</div>
          <div class="meta">${r.module} · ${fmt(r.value, r.unit)}</div>
        </div>
        <div class="risk-score">${r.score}</div>
      `;
      topRisks.appendChild(div);
    });
  }

  const modules = document.getElementById('modules');
  modules.innerHTML = '';
  data.modules.forEach(m => {
    const div = document.createElement('div');
    div.className = 'module-card';
    div.innerHTML = `
      <div class="module-head">
        <div class="module-title">${m.name}</div>
        <div class="module-score">${m.score}</div>
      </div>
      <div class="bar"><div class="bar-inner ${m.status}" style="width:${Math.min(100, Math.max(0, m.score))}%"></div></div>
      <div class="module-meta">权重 ${m.weight}% · ${statusText[m.status] || m.status} · ${m.indicators.length} 个指标</div>
    `;
    modules.appendChild(div);
  });

  const tbody = document.getElementById('indicatorRows');
  tbody.innerHTML = '';
  data.modules.forEach(m => {
    m.indicators.forEach(ind => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${m.name}</td>
        <td>${ind.label}</td>
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
