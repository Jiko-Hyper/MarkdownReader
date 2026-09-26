"""Shared visual connection settings for the desktop and browser interfaces."""
import json
import os
import secrets

from .ai_api import AiApi
from .storage import atomic_write


def settings_path(api):
    return os.path.join(api.ws.root, 'ai-connection.json')


def restore(api):
    try:
        with open(settings_path(api), encoding='utf-8') as stream:
            saved = json.load(stream)
        if not isinstance(saved, dict) or type(saved.get('enabled')) is not bool or type(saved.get('writable')) is not bool:
            raise ValueError('连接设置格式错误')
        if saved['enabled']:
            api.ai = AiApi(api.ws, saved['token'], saved['writable'])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError):
        api.ai_settings_error = '之前的接入设置无法读取，接口已关闭。请重新开启。'


def status(api):
    with api.ai_lock:
        current = api.ai
        token = current.token if current else ''
        address = api.ai_base_url + '/api/ai/v1/tools' if api.ai_base_url else ''
        instructions = ('请通过本机 HTTP 工具接口连接 MDReader，帮助我处理项目文档。\n'
                        '工具列表地址：%s\n'
                        '请求头：Authorization: Bearer %s\n'
                        '调用地址：%s/api/ai/v1/tools/call\n'
                        'POST JSON 格式：{"name":"工具名","arguments":{}}\n'
                        '先获取工具列表，再按工具定义调用。修改前读取文档，使用返回的 revision 作为 expected。\n'
                        '权限：%s。仅访问已保存的项目文档。') % (
                            address, token, api.ai_base_url,
                            '允许修改' if current and current.writable else '只读') if current else ''
        return {'ok': True, 'enabled': current is not None,
                'writable': bool(current and current.writable), 'address': address,
                'token': token, 'instructions': instructions,
                'last_access': getattr(current, 'last_access', ''), 'error': api.ai_settings_error}


def configure(api, payload):
    if not isinstance(payload, dict) or set(payload) - {'enabled', 'writable', 'reset_key'}:
        raise ValueError('无效接入设置')
    if any(type(value) is not bool for value in payload.values()):
        raise ValueError('接入设置必须是开关值')
    with api.ai_lock:
        old = api.ai
        enabled = payload.get('enabled', old is not None)
        writable = payload.get('writable', bool(old and old.writable))
        if payload.get('reset_key') and not enabled:
            raise ValueError('请先开启 AI 接入')
        token = old.token if old and not payload.get('reset_key') else secrets.token_urlsafe(32)
        new = AiApi(api.ws, token, writable) if enabled else None
        if new and old and new.token == old.token:
            new.last_access = old.last_access
        # Persist before publishing, so failure leaves the working connection unchanged.
        atomic_write(settings_path(api), json.dumps({'enabled': enabled, 'writable': writable if enabled else False,
                                                     'token': token if enabled else ''}, ensure_ascii=False))
        api.ai = new
        api.ai_settings_error = ''
        return status(api)
