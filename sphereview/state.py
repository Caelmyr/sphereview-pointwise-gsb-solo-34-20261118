from copy import deepcopy
from uuid import uuid4
import re

from image_lab import validation as v
from .projection import ASPECTS

DEFAULT_VIEW = {'yaw': 0, 'pitch': 0, 'roll': 0, 'fov': 72, 'aspect': '16:9'}
DEFAULT_POSE = {'yaw': 0, 'pitch': 0, 'roll': 0}
DEFAULT_PLANET = {'spin': 0, 'radius': 1.2}
MAX_MARKERS = 6
OMIT = object()


def pose_data(values, pitch_limit=89.5):
    data = v.object_value(values, '方向')
    return {
        'yaw': v.number(data.get('yaw'), '水平角', -180, 180),
        'pitch': v.number(data.get('pitch'), '仰角', -pitch_limit, pitch_limit),
        'roll': v.number(data.get('roll'), '旋转角', -180, 180),
    }


def view_data(values):
    data = v.object_value(values, '取景')
    return {
        **pose_data(data),
        'fov': v.number(data.get('fov'), '水平视角', 24, 140),
        'aspect': v.choice(data.get('aspect'), '画幅', ASPECTS),
    }


def planet_data(values):
    data = v.object_value(values, '小行星参数')
    return {
        'spin': v.number(data.get('spin'), '小行星旋转角', -180, 180),
        'radius': v.number(data.get('radius'), '小行星比例', 0.4, 2.5),
    }


def project_name(value):
    return v.text(value, '项目名称', 80, empty=False)


def clean_bookmark(spec):
    data = v.object_value(spec, '取景书签')
    return {'id': v.identifier(data.get('id')),
            'name': v.text(data.get('name'), '书签名称', 50, empty=False),
            'view': view_data(data.get('view'))}


def marker_label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9A-Za-z]{1,4}', value):
        raise v.Invalid('编号需为1至4位字母或数字，例如 A1、B2')
    return value.upper()


def clean_marker(spec):
    data = v.object_value(spec, '入口标注')
    return {
        'id': v.identifier(data.get('id')),
        'label': marker_label(data.get('label')),
        'yaw': v.number(data.get('yaw'), '入口水平角', -180, 180),
        'pitch': v.number(data.get('pitch'), '入口仰角', -89.5, 89.5),
        'priority': v.boolean(data.get('priority'), '重点标记'),
    }


def rehydrate(edit=OMIT):
    if edit is OMIT:
        return {'view': DEFAULT_VIEW.copy(), 'pose': DEFAULT_POSE.copy(),
                'planet': DEFAULT_PLANET.copy(), 'exposure': 0, 'bookmarks': [],
                'markers': []}
    record = v.object_value(edit, '工作区')
    bookmarks = [clean_bookmark(row) for row in v.list_value(record.get('bookmarks'), '书签', 36)]
    if len({row['id'] for row in bookmarks}) != len(bookmarks):
        raise v.Invalid('书签编号重复')
    markers = [clean_marker(row) for row in v.list_value(record.get('markers', []), '入口标注',
                                                          MAX_MARKERS)]
    marker_ids = {row['id'] for row in markers}
    if len(marker_ids) != len(markers):
        raise v.Invalid('入口标注编号重复')
    labels = [row['label'] for row in markers]
    if len(set(labels)) != len(labels):
        raise v.Invalid('入口编号不能重复')
    return {
        'view': view_data(record.get('view')),
        'pose': pose_data(record.get('pose'), 85),
        'planet': planet_data(record.get('planet')),
        'exposure': v.number(record.get('exposure'), '曝光', -3, 3),
        'bookmarks': bookmarks,
        'markers': markers,
    }


def find_bookmark(project, value):
    key = v.identifier(value)
    for item in project['bookmarks']:
        if item['id'] == key:
            return item
    raise v.Invalid('书签不存在')


def find_marker(project, value):
    key = v.identifier(value)
    for item in project['markers']:
        if item['id'] == key:
            return item
    raise v.Invalid('入口标注不存在')


def patch(project, action, values):
    body = v.object_value(values)
    if action == 'view':
        project['view'] = view_data(body.get('view'))
    elif action == 'pose':
        project['pose'] = pose_data(body.get('pose'), 85)
    elif action == 'exposure':
        project['exposure'] = v.number(body.get('exposure'), '曝光', -3, 3)
    elif action == 'planet':
        project['planet'] = planet_data(body.get('planet'))
    elif action == 'name':
        project['name'] = project_name(body.get('name'))
    elif action == 'bookmark':
        if len(project['bookmarks']) == 36:
            raise v.Invalid('每个项目最多36张书签')
        name = v.text(body.get('name'), '书签名称', 50, empty=False)
        project['bookmarks'].append({'id': uuid4().hex, 'name': name, 'view': deepcopy(project['view'])})
    elif action == 'recall':
        item = find_bookmark(project, body.get('bookmark'))
        project['view'] = deepcopy(item['view'])
    elif action == 'delete-bookmark':
        project['bookmarks'].remove(find_bookmark(project, body.get('bookmark')))
    elif action == 'marker':
        if len(project['markers']) == MAX_MARKERS:
            raise v.Invalid(f'每个项目最多{MAX_MARKERS}个入口标注')
        label = marker_label(body.get('label'))
        if any(item['label'] == label for item in project['markers']):
            raise v.Invalid('入口编号不能重复')
        project['markers'].append({
            'id': uuid4().hex, 'label': label,
            'yaw': v.number(body.get('yaw'), '入口水平角', -180, 180),
            'pitch': v.number(body.get('pitch'), '入口仰角', -89.5, 89.5),
            'priority': v.boolean(body.get('priority'), '重点标记'),
        })
    elif action == 'delete-marker':
        project['markers'].remove(find_marker(project, body.get('marker')))
    else:
        raise v.Invalid('操作不存在')
    return project
