/* DiskSentry 前端：从本地 API 拉取真实扫描数据并渲染仪表盘。 */
(function () {
  'use strict';
  var root = document.getElementById('dm-root');
  if (!root) return;
  var $ = function (s) { return root.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(root.querySelectorAll(s)); };

  var S = {
    hours: 24,
    visible: {},        // root -> bool（图例开关）
    st: null,           // /api/state
    sum: null,          // /api/summary
    trend: null,        // /api/trend
    lastScanId: null,
    colorIdx: {}
  };

  // ------------------------------------------------------------ 工具
  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }

  function fmtBytes(b) {
    var a = Math.abs(b);
    if (a >= 1024 * 1024 * 1024) return (a / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
    if (a >= 1024 * 1024) return (a / (1024 * 1024)).toFixed(0) + ' MB';
    if (a >= 1024) return (a / 1024).toFixed(0) + ' KB';
    return a + ' B';
  }
  function fmtDelta(b) {
    return (b > 0 ? '+' : b < 0 ? '-' : '') + fmtBytes(b);
  }
  function fmtGB(b) {
    var v = b / (1024 * 1024 * 1024);
    return (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
  }
  function pad2(n) { return String(n).padStart(2, '0'); }
  function timeStr(d) { return pad2(d.getHours()) + ':' + pad2(d.getMinutes()); }
  function dayTimeStr(d) { return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + timeStr(d); }
  function agoStr(ts) {
    var m = Math.max(0, Math.round((Date.now() - ts * 1000) / 60000));
    if (m < 1) return '刚刚';
    if (m < 60) return m + ' 分钟前';
    if (m < 1440) return Math.round(m / 60) + ' 小时前';
    return dayTimeStr(new Date(ts * 1000));
  }
  function rootLetter(root) {
    if (/^[A-Za-z]:\\?$/.test(root)) return root.slice(0, 2);
    var parts = root.replace(/\\+$/, '').split('\\');
    return parts[parts.length - 1] || root;
  }
  function colorCls(r) {
    if (!(r in S.colorIdx) && S.st) {
      var disks = S.st.config.disks || [];
      S.colorIdx[r] = 'dm-c' + ((disks.indexOf(r) + 1) || 1);
    }
    return S.colorIdx[r] || 'dm-c1';
  }
  function isMonitored(r) {
    return !!(S.st && (S.st.config.disks || []).indexOf(r) >= 0);
  }

  // ------------------------------------------------------------ 状态/卡片
  function renderStatus() {
    var el = $('#dm-status');
    var txt;
    if (!S.st) { txt = '加载中…'; }
    else if (S.st.scanning && S.st.scanning.running) {
      var cur = S.st.scanning.current_root || '';
      var n = S.st.scanning.entries || 0;
      txt = '正在扫描 ' + (cur ? rootLetter(cur) : '…') +
        (n ? ' · ' + n.toLocaleString() + ' 项' : '');
    } else if (S.st.last_scan && S.st.last_scan.finished_at) {
      var ls = S.st.last_scan;
      var t = new Date(ls.finished_at * 1000);
      var tLabel = (Date.now() - ls.finished_at * 1000 < 86400 * 1000)
        ? timeStr(t) : dayTimeStr(t);
      txt = '上次扫描 ' + tLabel + '（' + agoStr(ls.finished_at) + '）' +
        (ls.status === 'error' ? ' · 失败' : ls.status === 'partial' ? ' · 部分失败' : '') +
        ' · 间隔 ' + S.st.config.interval_minutes + ' 分钟';
    } else {
      txt = '等待首次扫描…';
    }
    $('#dm-status-text').textContent = txt;
    el.classList.toggle('scanning', !!(S.st && S.st.scanning && S.st.scanning.running));
    $('#dm-scan-btn').disabled = !S.st || !!(S.st.scanning && S.st.scanning.running);

    var chip = $('#dm-alert-chip');
    var alerts = (S.st && S.st.alerts) || [];
    if (alerts.length) {
      chip.hidden = false;
      chip.textContent = '⚠ 告警 ' + alerts.length;
      chip.title = alerts.map(function (a) {
        return dayTimeStr(new Date(a.ts * 1000)) + ' ' + a.message;
      }).join('\n');
    } else {
      chip.hidden = true;
    }
    if (S.st && S.st.version) {
      $('#dm-version').hidden = false;
      $('#dm-version').textContent = 'v' + S.st.version;
    }
  }

  function renderCards() {
    var el = $('#dm-cards');
    if (!S.st) return;
    var deltas = (S.sum && S.sum.deltas) || {};
    el.innerHTML = S.st.available.map(function (d) {
      var on = isMonitored(d.root);
      var pct = d.total ? Math.round(d.used / d.total * 100) : null;
      var hot = pct !== null && pct >= 90;
      var cls = colorCls(d.root);
      var delta = deltas[d.root];
      var deltaHtml = on
        ? (delta == null ? '—'
          : delta >= 0
            ? '<b class="dm-delta-up">' + fmtDelta(delta) + '</b>'
            : '<b class="dm-delta-down">' + fmtDelta(delta) + '</b>')
        : '—';
      var label = d.label ? d.label : '';
      return '<div class="dm-card' + (on ? '' : ' dm-off') + '">' +
        '<div class="dm-card-top">' +
          '<span class="dm-diskletter ' + cls + '">' + rootLetter(d.root) + '</span>' +
          '<span class="dm-disklabel">' + label + '</span>' +
          (on ? '' : '<span class="dm-off-badge">未监控</span>') +
        '</div>' +
        '<div class="dm-bar"><div class="dm-bar-fill' + (hot ? ' hot' : '') + '" style="width:' + (pct === null ? 0 : Math.min(pct, 100)) + '%"></div></div>' +
        '<div class="dm-card-nums dm-num">' +
          '<span>' + (d.used == null ? '—' : fmtBytes(d.used) + ' 已用') + '</span>' +
          '<span class="dm-muted">/ ' + (d.total == null ? '—' : fmtBytes(d.total)) +
            (pct === null ? '' : ' · ' + pct + '%') + '</span></div>' +
        '<div class="dm-card-delta">近 ' + windowLabel(S.hours) + ' ' + deltaHtml + '</div>' +
      '</div>';
    }).join('');
  }

  function windowLabel(h) {
    if (h < 24) return h + ' 小时';
    if (h < 720) return (h / 24) + ' 天';
    return (h / 720) + ' 个月';
  }

  function renderLegend() {
    var el = $('#dm-legend');
    if (!S.st) return;
    el.innerHTML = S.st.config.disks.map(function (r) {
      var vis = S.visible[r] !== false;
      return '<button type="button" class="dm-leg" data-root="' + r + '"' +
        ' aria-pressed="' + vis + '">' +
        '<span class="dm-dot ' + colorCls(r) + '"></span>' + rootLetter(r) + '</button>';
    }).join('');
  }

  function renderFooter() {
    if (!S.st) return;
    var mb = S.st.db_size / (1024 * 1024);
    $('#dm-footer').innerHTML =
      '<span>数据库 ' + (mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb.toFixed(1) + ' MB') + '</span>' +
      '<span>明细保留 ' + S.st.config.retention_days + ' 天</span>' +
      '<span>告警阈值 ' + S.st.config.alert_threshold_gb_per_hour + ' GB / 小时</span>' +
      '<span>后台低优先级扫描' + (S.st.config.only_when_idle ? ' · 仅空闲时' : '') + '</span>';
  }

  // ------------------------------------------------------------ 图表
  var chartWrap = $('#dm-chartwrap');
  var tip = $('#dm-tip');
  var svgEl = null, gMain = null, gx = null, gy = null, gZero = null,
      gLines = null, gGuide = null, gCircles = null, hitRect = null;
  var chartGeom = null;

  function initChart() {
    if (typeof d3 === 'undefined') {
      $('#dm-chart-empty').hidden = false;
      $('#dm-chart-empty').textContent = '图表组件加载失败';
      return false;
    }
    svgEl = d3.select(chartWrap).append('svg')
      .attr('style', 'display:block;width:100%')
      .attr('role', 'img').attr('aria-label', '磁盘容量净增长趋势图');
    gMain = svgEl.append('g');
    gy = gMain.append('g').attr('class', 'dm-axis dm-grid');
    gx = gMain.append('g').attr('class', 'dm-axis');
    gZero = gMain.append('line').attr('class', 'dm-zero');
    gLines = gMain.append('g');
    gGuide = gMain.append('g').style('display', 'none');
    gGuide.append('line').attr('class', 'dm-zero');
    gCircles = gGuide.append('g');
    hitRect = gMain.append('rect').attr('class', 'dm-hit');
    hitRect.on('pointermove', onChartMove).on('pointerleave', hideTip);
    return true;
  }

  function visibleSeries() {
    if (!S.trend || !S.st) return [];
    return S.trend.series.filter(function (s) {
      return isMonitored(s.root) && S.visible[s.root] !== false && s.points.length > 0;
    }).map(function (s) {
      return {
        root: s.root,
        cls: colorCls(s.root),
        points: s.points.map(function (p) { return { t: p.t, v: p.v / (1024 * 1024 * 1024) }; })
      };
    });
  }

  function drawChart() {
    if (!svgEl) return;
    var w = Math.max(chartWrap.clientWidth, 60);
    var h = 264;
    var m = { t: 12, r: 16, b: 30, l: 56 };
    var iw = w - m.l - m.r, ih = h - m.t - m.b;
    svgEl.attr('viewBox', '0 0 ' + w + ' ' + h).attr('width', w).attr('height', h);
    gMain.attr('transform', 'translate(' + m.l + ',' + m.t + ')');

    var series = visibleSeries();
    var hasAny = S.trend && S.trend.series.length > 0;
    var empty = $('#dm-chart-empty');
    empty.hidden = series.length > 0;
    if (!hasAny) empty.textContent = '暂无数据 · 等待首次扫描';
    else if (series.length === 0) empty.textContent = '图例中的磁盘均已隐藏';
    svgEl.style('display', series.length ? null : 'none');
    if (!series.length) { chartGeom = null; hideTip(); return; }

    var x = d3.scaleTime()
      .domain([Date.now() - S.hours * 3600 * 1000, Date.now()])
      .range([0, iw]);
    var lo = 0, hi = 0;
    series.forEach(function (s) {
      s.points.forEach(function (p) { if (p.v < lo) lo = p.v; if (p.v > hi) hi = p.v; });
    });
    if (hi - lo < 0.05) { hi += 0.05; lo -= 0.05; }
    var pad = (hi - lo) * 0.14;
    var y = d3.scaleLinear()
      .domain([Math.min(0, lo) - (lo < 0 ? pad : 0), Math.max(0.05, hi + pad)])
      .range([ih, 0]);

    var dayMode = S.hours >= 168;
    var rangeGB = hi - lo;
    var tickFmt = rangeGB < 1
      ? function (v) { return (v > 0 ? '+' : '') + Math.round(v * 1024) + ' MB'; }
      : function (v) { return (v > 0 ? '+' : '') + fmtGB(v * 1024 * 1024 * 1024); };
    gx.attr('transform', 'translate(0,' + ih + ')').call(
      d3.axisBottom(x).ticks(6).tickFormat(function (t) {
        var d = new Date(t);
        return dayMode ? pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) : timeStr(d);
      }));
    gy.call(
      d3.axisLeft(y).ticks(5).tickSize(-iw).tickFormat(tickFmt));

    gZero.attr('x1', 0).attr('x2', iw).attr('y1', y(0)).attr('y2', y(0))
      .style('display', (lo < 0 && hi > 0) ? null : 'none');

    var line = d3.line().x(function (p) { return x(p.t); }).y(function (p) { return y(p.v); })
      .curve(d3.curveMonotoneX);
    var binding = gLines.selectAll('path.dm-line').data(series, function (s) { return s.root; });
    binding.enter().append('path')
      .attr('class', function (s) { return 'dm-line ' + s.cls; })
      .merge(binding)
      .attr('d', function (s) { return line(s.points); });
    binding.exit().remove();

    hitRect.attr('x', 0).attr('y', 0).attr('width', iw).attr('height', ih);
    gGuide.select('line').attr('y1', 0).attr('y2', ih);
    chartGeom = { m: m, w: w, iw: iw, ih: ih, x: x, y: y, series: series };
    hideTip();
  }

  var bisectT = (typeof d3 !== 'undefined')
    ? d3.bisector(function (p) { return p.t; }).center : null;

  function onChartMove(ev) {
    if (!chartGeom) return;
    var g = chartGeom;
    var mx = Math.max(0, Math.min(g.iw, d3.pointer(ev, gMain.node())[0]));
    var t = g.x.invert(mx);
    var rows = [], marks = [];
    g.series.forEach(function (s) {
      var i = bisectT(s.points, t);
      var p = s.points[i];
      if (!p) return;
      marks.push({ root: s.root, cls: s.cls, cx: g.x(p.t), cy: g.y(p.v) });
      rows.push('<span><span class="dm-dot ' + s.cls + '"></span>' +
        rootLetter(s.root) + ' <b class="dm-num">' +
        (p.v > 0 ? '+' : p.v < 0 ? '-' : '') + fmtBytes(p.v * 1024 * 1024 * 1024) + '</b></span>');
    });
    if (!rows.length) { hideTip(); return; }
    gGuide.style('display', null);
    gGuide.select('line').attr('x1', mx).attr('x2', mx);
    var circ = gCircles.selectAll('circle').data(marks, function (d) { return d.root; });
    circ.enter().append('circle').attr('r', 3.5)
      .attr('class', function (d) { return d.cls; })
      .merge(circ)
      .attr('cx', function (d) { return d.cx; })
      .attr('cy', function (d) { return d.cy; });
    circ.exit().remove();

    var d = new Date(t);
    tip.innerHTML = '<div class="dm-muted" style="margin-bottom:2px">' +
      (S.hours >= 168 ? dayTimeStr(d) : timeStr(d)) + '</div>' + rows.join('<br>');
    tip.style.display = 'block';
    var relX = g.m.l + mx;
    var tw = tip.offsetWidth;
    var left = relX + 14;
    if (left + tw > g.w + g.m.l - 6) left = relX - tw - 14;
    if (left < 2) left = 2;
    tip.style.left = left + 'px';
    tip.style.top = '8px';
  }
  function hideTip() {
    if (gGuide) gGuide.style('display', 'none');
    tip.style.display = 'none';
  }

  // ------------------------------------------------------------ 排行/表格
  function legendVisible(root) {
    return isMonitored(root) && S.visible[root] !== false;
  }

  function renderRank() {
    var el = $('#dm-rank');
    if (!S.sum) {
      el.innerHTML = '<div class="dm-empty">加载中…</div>';
      return;
    }
    // 排行跟随图例选择的盘符过滤（服务端多取一些，过滤后展示前 8）
    var items = S.sum.top_dirs.filter(function (r) { return legendVisible(r.root); })
      .slice(0, 8);
    if (!S.sum.top_dirs.length) {
      el.innerHTML = '<div class="dm-empty">暂无数据</div>';
      return;
    }
    if (!items.length) {
      el.innerHTML = '<div class="dm-empty">所选磁盘在窗口内无增长目录</div>';
      return;
    }
    var max = items.reduce(function (a, r) { return Math.max(a, r.delta); }, 1);
    el.innerHTML = items.map(function (r) {
      var pct = Math.max(3, Math.round(r.delta / max * 100));
      return '<div class="dm-rank-row">' +
        '<div class="dm-rank-path" title="' + r.path + '"><span class="dm-dot ' + colorCls(r.root) + '"></span>' + r.path + '</div>' +
        '<div class="dm-rank-val dm-num">' + fmtDelta(r.delta) + '</div>' +
        '<div class="dm-rank-bar"><div class="dm-rank-fill ' + colorCls(r.root) + '" style="width:' + pct + '%"></div></div>' +
      '</div>';
    }).join('');
  }

  var KINDS = { new: ['新建', 'dm-ev-new'], grow: ['增长', 'dm-ev-grow'],
                shrink: ['缩小', 'dm-ev-shrink'], delete: ['删除', 'dm-ev-delete'] };
  function renderTable() {
    var el = $('#dm-tbody');
    if (!S.sum || !S.sum.changes.length) {
      el.innerHTML = '<tr><td colspan="5" class="dm-empty">' + (S.sum ? '该时间窗内暂无变更' : '加载中…') + '</td></tr>';
      return;
    }
    var withDate = S.hours >= 168;
    el.innerHTML = S.sum.changes.map(function (r) {
      var ev = KINDS[r.kind] || [r.kind, ''];
      var dCls = r.delta > 0 ? 'dm-delta-up' : r.delta < 0 ? 'dm-delta-down' : '';
      var t = new Date(r.ts * 1000);
      var cur = (r.kind === 'delete') ? '—'
        : (r.new_size != null ? fmtBytes(r.new_size) : '—');
      return '<tr>' +
        '<td class="dm-num dm-muted">' + (withDate ? dayTimeStr(t) : timeStr(t)) + '</td>' +
        '<td><span class="dm-ev ' + ev[1] + '">' + ev[0] + '</span></td>' +
        '<td class="dm-mono dm-path" title="' + r.path + '">' + r.path + '</td>' +
        '<td class="dm-num ' + dCls + '" style="text-align:right">' + fmtDelta(r.delta) + '</td>' +
        '<td class="dm-num dm-muted" style="text-align:right">' + cur + '</td>' +
      '</tr>';
    }).join('');
  }

  // ------------------------------------------------------------ 数据加载
  function loadState() {
    return api('/api/state').then(function (st) {
      var prevId = S.lastScanId;
      S.st = st;
      if (st.last_scan) S.lastScanId = st.last_scan.id;
      renderStatus(); renderCards(); renderLegend(); renderFooter();
      if (prevId != null && S.lastScanId != null && prevId !== S.lastScanId) {
        return loadSummary().then(loadTrend);
      }
    });
  }
  function loadSummary() {
    // topn 多取一些：前端要按图例所选盘符过滤后再展示前 8
    return api('/api/summary?window=' + S.hours + '&topn=24').then(function (sum) {
      S.sum = sum;
      renderCards(); renderRank(); renderTable();
    });
  }
  function loadTrend() {
    return api('/api/trend?window=' + S.hours).then(function (tr) {
      S.trend = tr;
      drawChart();
    });
  }
  function loadAll() {
    return Promise.all([loadState(), loadSummary().then(loadTrend)])
      .catch(function (e) {
        $('#dm-status-text').textContent = '连接服务失败：' + e.message;
      });
  }

  // ------------------------------------------------------------ 事件
  $$('.dm-pill').forEach(function (b) {
    b.addEventListener('click', function () {
      S.hours = parseInt(b.dataset.hours, 10);
      $$('.dm-pill').forEach(function (x) {
        x.setAttribute('aria-pressed', x === b ? 'true' : 'false');
      });
      loadSummary().then(loadTrend).catch(function () {});
    });
  });

  $('#dm-legend').addEventListener('click', function (e) {
    var btn = e.target.closest('.dm-leg');
    if (!btn) return;
    var r = btn.dataset.root;
    S.visible[r] = S.visible[r] === false;
    renderLegend();
    drawChart();
    renderRank();
  });

  $('#dm-scan-btn').addEventListener('click', function () {
    api('/api/scan', { method: 'POST' })
      .then(function () {
        renderStatus();
        var tries = 0;
        (function poll() {
          setTimeout(function () {
            loadState().then(function () {
              var running = S.st && S.st.scanning && S.st.scanning.running;
              if (running && tries++ < 600) poll();
            }).catch(function () {});
          }, 1500);
        })();
      })
      .catch(function (e) { $('#dm-status-text').textContent = '触发失败：' + e.message; });
  });

  // 设置对话框
  var backdrop = $('#dm-backdrop');
  var lastFocus = null;
  function openDialog() {
    if (!S.st) return;
    lastFocus = document.activeElement;
    $('#dm-disk-checks').innerHTML = S.st.available.map(function (d) {
      var name = rootLetter(d.root) + (d.label ? ' · ' + d.label : '');
      return '<label class="dm-check"><input type="checkbox" data-root="' + d.root + '"' +
        (isMonitored(d.root) ? ' checked' : '') + '>' +
        '<span><b class="' + colorCls(d.root) + '">' + name + '</b></span>' +
        '<span class="dm-cap dm-num">' +
        (d.total == null ? '' : fmtBytes(d.total)) + '</span></label>';
    }).join('');
    var sel = $('#dm-interval');
    var val = String(S.st.config.interval_minutes);
    if (![].map.call(sel.options, function (o) { return o.value; }).includes(val)) {
      var opt = document.createElement('option');
      opt.value = val; opt.textContent = '每 ' + val + ' 分钟';
      sel.appendChild(opt);
    }
    sel.value = val;
    $('#dm-threshold').value = String(S.st.config.alert_threshold_gb_per_hour);
    $('#dm-idle').checked = !!S.st.config.only_when_idle;
    backdrop.hidden = false;
    var first = $('#dm-disk-checks input');
    if (first) first.focus();
  }
  function closeDialog() {
    backdrop.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  $('#dm-settings-btn').addEventListener('click', openDialog);
  $('#dm-dialog-close').addEventListener('click', closeDialog);
  $('#dm-cancel').addEventListener('click', closeDialog);
  backdrop.addEventListener('click', function (e) { if (e.target === backdrop) closeDialog(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !backdrop.hidden) closeDialog();
  });
  $('#dm-save').addEventListener('click', function () {
    var disks = $$('#dm-disk-checks input').filter(function (cb) { return cb.checked; })
      .map(function (cb) { return cb.dataset.root; });
    if (!disks.length) { alert('至少选择一个监控磁盘'); return; }
    api('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        disks: disks,
        interval_minutes: parseInt($('#dm-interval').value, 10) || 60,
        alert_threshold_gb_per_hour: parseFloat($('#dm-threshold').value) || 5,
        only_when_idle: $('#dm-idle').checked
      })
    }).then(function () {
      closeDialog();
      return loadAll();
    }).catch(function (e) { alert('保存失败：' + e.message); });
  });

  // ------------------------------------------------------------ 启动
  if (initChart()) {
    if (typeof ResizeObserver !== 'undefined') {
      var queued = false;
      new ResizeObserver(function () {
        if (queued) return;
        queued = true;
        requestAnimationFrame(function () { queued = false; drawChart(); });
      }).observe(chartWrap);
    }
  }
  loadAll();
  setInterval(function () { loadState().catch(function () {}); }, 30000);
})();
