'use strict';

const $ = id => document.getElementById(id);
const modes = {
    view: ['透视取景', 'view.png'],
    leveled: ['整站校准经纬图', 'leveled.png'],
    cube: ['六张定向方形贴图', 'cube.zip'],
    planet: ['地面居中的小行星投影', 'planet.png'],
    source: ['未经调整的经纬原图', 'source.png']
};
const faces = {
    left: '左', front: '前', right: '右', back: '后', top: '顶', bottom: '底'
};
let current = null;
let mode = 'view';
let busy = false;
let navToken = '';
let mapMode = 'navigate';
let pendingPoint = null;

function route(key, product = '') {
    return '/sphere/projects/' + key + (product ? '/' + product : '');
}

async function json(url, options) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '请求失败');
    return body;
}

function post(url, content) {
    return json(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(content)});
}

function run(action) {
    return async event => {
        if (event) event.preventDefault();
        if (busy) return;
        busy = true;
        $('status').textContent = '';
        try {
            await action(event);
        } catch (error) {
            $('status').textContent = error.message;
        } finally {
            busy = false;
        }
    };
}

function inputValues(form, keys) {
    return Object.fromEntries(keys.map(key => [key, form.elements[key].value]));
}

function setValues(form, values) {
    for (const [key, value] of Object.entries(values)) {
        form.elements[key].value = value;
    }
}

function showMode() {
    if (!current) return;
    $('planet-settings').hidden = mode !== 'planet';
    for (const button of $('modes').querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
    }
    const [description, filename] = modes[mode];
    $('mode-description').textContent = description;
    $('output').textContent = mode === 'cube' ? '下载六面 ZIP ↓' : '下载 PNG ↓';
    $('output').href = route(current.id, filename) + '?download=1';
    $('hero').hidden = mode === 'cube';
    $('cube-grid').hidden = mode !== 'cube';
    $('primary-image').classList.toggle('six-faces', mode === 'cube');
    if (mode === 'cube') {
        $('cube-grid').replaceChildren(...Object.entries(faces).map(([key, label]) => {
            const figure = document.createElement('figure');
            const image = document.createElement('img');
            image.src = route(current.id, 'cube/' + key + '.png') + '?r=' + current.revision;
            image.alt = label + '面';
            const caption = document.createElement('figcaption');
            caption.textContent = label;
            figure.append(image, caption);
            return figure;
        }));
    } else {
        $('hero').src = route(current.id, filename) + '?r=' + current.revision;
    }
    if (mode === 'view') {
        refreshViewLabels();
    } else {
        $('annotation-status').textContent = '';
    }
}

async function projectList() {
    const projects = await json('/sphere/projects');
    $('projects').replaceChildren(...projects.map(project => {
        const row = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = project.name;
        button.setAttribute('aria-current', current?.id === project.id ? 'true' : 'false');
        button.addEventListener('click', run(async () => {
            mode = 'view';
            setProject(await json(route(project.id)));
            await projectList();
        }));
        row.append(button);
        return row;
    }));
}

function setProject(project) {
    if (current?.id !== project.id) {
        $('probe-readout').textContent = '点击方向图转到指定位置并读取原图像素。';
        $('seam-result').replaceChildren();
    }
    current = project;
    $('workspace').hidden = false;
    $('empty').hidden = true;
    $('project-name').textContent = project.name;
    $('source-meta').textContent = [
        project.source.width + ' × ' + project.source.height + ' px',
        project.synthetic ? '合成演示' : '导入照片',
        '修订 ' + project.revision
    ].join('  ·  ');
    $('project-zip').href = route(project.id, 'project.zip');
    setValues($('view-form'), project.view);
    setValues($('pose-form'), project.pose);
    setValues($('planet-form'), project.planet);
    setValues($('exposure-form'), {exposure: project.exposure});
    $('new-name').value = project.name;
    const token = JSON.stringify([project.id, project.pose, project.exposure]);
    if (token !== navToken) {
        $('navigation-image').src = route(project.id, 'leveled.png') + '?r=' + project.revision;
        navToken = token;
        $('probe-readout').textContent = '点击方向图转到指定位置并读取原图像素。';
    }
    $('marker').style.left = (project.view.yaw + 180) / 3.6 + '%';
    $('marker').style.top = (90 - project.view.pitch) / 1.8 + '%';
    populateMarkers();
    renderMapBadges();
    populateBookmarks();
    showMode();
}

async function edit(action, values) {
    const data = await post(route(current.id, 'edit'), {revision: current.revision, action, ...values});
    setProject(data);
}

function populateBookmarks() {
    const cards = current.bookmarks.map(item => {
        const card = document.createElement('article');
        const image = document.createElement('img');
        image.alt = item.name + '的取景';
        image.loading = 'lazy';
        image.src = route(current.id, 'bookmark/' + item.id + '.png') + '?r=' + current.revision;
        const info = document.createElement('div');
        const title = document.createElement('h4');
        title.textContent = item.name;
        const recall = document.createElement('button');
        recall.textContent = '切换取景';
        recall.addEventListener('click', run(async () => {
            mode = 'view';
            await edit('recall', {bookmark: item.id});
        }));
        const remove = document.createElement('button');
        remove.textContent = '移除';
        remove.addEventListener('click', run(() => edit('delete-bookmark', {bookmark: item.id})));
        info.append(title, recall, remove);
        card.append(image, info);
        return card;
    });
    $('bookmark-list').replaceChildren(...cards);
}

function setMapMode(next) {
    mapMode = next;
    for (const button of document.querySelectorAll('.map-mode')) {
        button.setAttribute('aria-pressed', String(button.dataset.mapMode === mapMode));
    }
    $('direction-map').classList.toggle('picking', mapMode === 'annotate');
    if (mapMode === 'navigate') {
        pendingPoint = null;
        $('pending-dot').hidden = true;
        $('probe-readout').textContent = '点击方向图转到指定位置并读取原图像素。';
    } else {
        $('probe-readout').textContent = '在方向图上点选入口位置，然后填写编号保存。';
    }
}

function populateMarkers() {
    const markers = current.markers;
    $('marker-count').textContent = markers.length + ' / 6';
    const full = markers.length >= 6;
    $('marker-label').disabled = full;
    $('marker-priority').disabled = full;
    $('marker-form').querySelector('button').disabled = full;
    $('marker-hint').textContent = full
        ? '已达六个入口上限，删除后才能继续添加。'
        : (pendingPoint
            ? `已选位置 ${pendingPoint.yaw.toFixed(1)}° / ${pendingPoint.pitch.toFixed(1)}°，填写编号后保存。`
            : '切到“添加入口”后在方向图上点选位置，再填写编号保存。');
    const rows = markers.map(marker => {
        const row = document.createElement('li');
        row.className = 'marker-row' + (marker.priority ? ' is-priority' : '');
        const badge = document.createElement('span');
        badge.className = 'marker-badge' + (marker.priority ? ' priority' : '');
        badge.textContent = marker.label;
        const angles = document.createElement('span');
        angles.className = 'marker-angles';
        angles.textContent = `${marker.yaw.toFixed(1)}° / ${marker.pitch.toFixed(1)}°${marker.priority ? ' · 重点' : ''}`;
        const controls = document.createElement('span');
        const locate = document.createElement('button');
        locate.type = 'button';
        locate.textContent = '对准';
        locate.addEventListener('click', run(async () => {
            mode = 'view';
            await edit('view', {view: {...current.view, yaw: marker.yaw, pitch: marker.pitch}});
        }));
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '删除';
        remove.addEventListener('click', run(() => edit('delete-marker', {marker: marker.id})));
        controls.append(locate, remove);
        row.append(badge, angles, controls);
        return row;
    });
    $('marker-list').replaceChildren(...rows);
}

async function renderMapBadges() {
    const layer = $('map-badges');
    const lines = $('map-lines');
    layer.replaceChildren();
    lines.replaceChildren();
    if (!current || current.markers.length === 0) return;
    const plan = await json(route(current.id, 'map-labels') + '?r=' + current.revision);
    const svgNS = 'http://www.w3.org/2000/svg';
    lines.setAttribute('viewBox', `0 0 ${plan.width} ${plan.height}`);
    lines.setAttribute('preserveAspectRatio', 'none');
    for (const label of plan.labels) {
        const a = label.anchor;
        const [x0, y0, x1, y1] = label.rect;
        const ax = a.x * plan.width;
        const ay = a.y * plan.height;
        const insideAnchor = ax >= x0 * plan.width && ax <= x1 * plan.width
            && ay >= y0 * plan.height && ay <= y1 * plan.height;
        if (!insideAnchor) {
            const targetX = Math.min(Math.max(ax, x0 * plan.width), x1 * plan.width);
            const targetY = Math.min(Math.max(ay, y0 * plan.height), y1 * plan.height);
            const line = document.createElementNS(svgNS, 'line');
            line.setAttribute('x1', ax);
            line.setAttribute('y1', ay);
            line.setAttribute('x2', targetX);
            line.setAttribute('y2', targetY);
            line.setAttribute('class', 'map-leader');
            lines.appendChild(line);
        }
        const dot = document.createElement('span');
        dot.className = 'map-dot';
        dot.style.left = (a.x * 100) + '%';
        dot.style.top = (a.y * 100) + '%';
        layer.appendChild(dot);
        const badge = document.createElement('span');
        badge.className = 'map-badge' + (label.priority ? ' priority' : '');
        badge.textContent = label.label;
        badge.style.left = (x0 * 100) + '%';
        badge.style.top = (y0 * 100) + '%';
        badge.style.width = ((x1 - x0) * 100) + '%';
        badge.style.height = ((y1 - y0) * 100) + '%';
        badge.title = `${label.label} 入口`;
        layer.appendChild(badge);
    }
}

async function refreshViewLabels() {
    if (!current || mode !== 'view') return;
    const plan = await json(route(current.id, 'view-labels') + '?r=' + current.revision);
    const shown = plan.labels.map(label => label.label);
    const dropped = plan.dropped.map(label => label.label);
    const hidden = plan.hidden.map(label => label.label);
    const parts = [];
    parts.push(`画内标注 ${shown.length} 个：${shown.join('、') || '无'}`);
    if (dropped.length) parts.push(`空间不足未显示：${dropped.join('、')}`);
    if (hidden.length) parts.push(`画外：${hidden.join('、')}`);
    $('annotation-status').textContent = parts.join('  ·  ');
}

function bindUpload(open, input, path) {
    $(open).addEventListener('click', () => $(input).click());
    $(input).addEventListener('change', run(async event => {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file) return;
        const body = new FormData();
        body.append('file', file);
        mode = 'view';
        setProject(await json(path, {method: 'POST', body}));
        await projectList();
    }));
}

bindUpload('upload-open', 'upload', '/sphere/projects');
bindUpload('restore-open', 'restore', '/sphere/restore');
$('demo').addEventListener('click', run(async () => {
    mode = 'view';
    setProject(await post('/sphere/demo', {}));
    await projectList();
}));

for (const button of $('modes').querySelectorAll('button')) {
    button.addEventListener('click', () => {
        mode = button.dataset.mode;
        showMode();
    });
}

$('view-form').addEventListener('submit', run(event => {
    mode = 'view';
    return edit('view', {view: inputValues(event.target, ['yaw', 'pitch', 'roll', 'fov', 'aspect'])});
}));
$('pose-form').addEventListener('submit', run(event => {
    mode = 'leveled';
    return edit('pose', {pose: inputValues(event.target, ['yaw', 'pitch', 'roll'])});
}));
$('planet-form').addEventListener('submit', run(event => edit('planet', {
    planet: inputValues(event.target, ['spin', 'radius'])
})));
$('exposure-form').addEventListener('submit', run(event => edit('exposure', inputValues(event.target, ['exposure']))));
$('bookmark-form').addEventListener('submit', run(async () => {
    await edit('bookmark', {name: $('bookmark-name').value});
    $('bookmark-name').value = '';
}));
$('rename-form').addEventListener('submit', run(async () => {
    await edit('name', {name: $('new-name').value});
    await projectList();
}));

$('direction-map').addEventListener('click', run(async event => {
    const box = $('direction-map').getBoundingClientRect();
    const yaw = +(((event.clientX - box.left) / box.width) * 360 - 180).toFixed(2);
    const pitch = Math.min(89.5, Math.max(-89.5, +(90 - ((event.clientY - box.top) / box.height) * 180).toFixed(2)));
    if (mapMode === 'annotate') {
        if (current.markers.length >= 6) {
            $('status').textContent = '每个项目最多保存六个入口标注';
            return;
        }
        pendingPoint = {yaw, pitch};
        const dot = $('pending-dot');
        dot.hidden = false;
        dot.style.left = ((yaw + 180) / 3.6) + '%';
        dot.style.top = ((90 - pitch) / 1.8) + '%';
        $('marker-label').focus();
        populateMarkers();
        return;
    }
    mode = 'view';
    await edit('view', {view: {...current.view, yaw, pitch}});
    const search = new URLSearchParams({yaw, pitch});
    const probe = await json(route(current.id, 'probe') + '?' + search);
    $('probe-readout').textContent = '原图像素 (' + probe.column + ', ' + probe.row +
        ')  ·  RGBA ' + probe.rgba.join(', ');
}));

for (const button of document.querySelectorAll('.map-mode')) {
    button.addEventListener('click', () => setMapMode(button.dataset.mapMode));
}

$('marker-form').addEventListener('submit', run(async event => {
    event.preventDefault();
    if (!pendingPoint) {
        $('status').textContent = '请先切到“添加入口”并在方向图上点选位置';
        return;
    }
    await edit('marker', {
        label: $('marker-label').value,
        yaw: pendingPoint.yaw,
        pitch: pendingPoint.pitch,
        priority: $('marker-priority').checked,
    });
    $('marker-label').value = '';
    $('marker-priority').checked = false;
    pendingPoint = null;
    $('pending-dot').hidden = true;
    populateMarkers();
}));

$('seam').addEventListener('click', run(async () => {
    const report = await json(route(current.id, 'seam'));
    const headline = document.createElement('p');
    const differences = report.mean_rgb_difference == null ? '无有效对照' : report.mean_rgb_difference;
    headline.textContent = `有效行 ${report.valid_rows} / ${report.rows}，平均差 ${differences}`;
    const table = document.createElement('table');
    table.setAttribute('aria-label', '按行段的接缝差');
    for (const band of report.bands) {
        const row = table.insertRow();
        row.insertCell().textContent = `${band.row_start}–${band.row_stop - 1} 行`;
        row.insertCell().textContent = band.mean_rgb_difference ?? '—';
    }
    $('seam-result').replaceChildren(headline, table);
}));

setMapMode('navigate');
run(projectList)();
