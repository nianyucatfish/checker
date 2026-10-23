#!/usr/bin/env python3
"""Audio QC local REST client. Python 3.9+, standard library only."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def discovery_path():
    override = os.environ.get('CHECKER_EXTERNAL_CONNECTION')
    if override:
        return Path(override)
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/AudioQC/external-agent.json'
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', str(Path.home()))) / 'AudioQC/external-agent.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'AudioQC/external-agent.json'


class Client:
    def __init__(self, file):
        try:
            self.connection = json.loads(Path(file).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ValueError('未找到有效连接信息，请先打开支持外部接口的 Audio QC；也可用 --connection-file 指定') from None
        url = urllib.parse.urlsplit(self.connection.get('url', ''))
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path or url.query or url.fragment or url.username or url.password:
            raise ValueError('连接文件必须指向本机 127.0.0.1 HTTP 服务')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        status = self.request('/v1/status')
        if status.get('instance_id') != self.connection.get('instance_id') or status.get('protocol') != 1:
            raise ValueError('软件实例已变化，请重新读取连接文件')

    def request(self, route, data=None):
        request = urllib.request.Request(self.connection['url'] + route,
            headers={'Authorization': 'Bearer ' + self.connection['token'], 'Content-Type': 'application/json'},
            data=None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8'))
        try:
            with self.opener.open(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            try: message = json.load(e).get('message', str(e.code))
            except (ValueError, AttributeError): message = str(e.code)
            raise ValueError(f'HTTP {e.code}: {message}') from None
        except OSError:
            raise ValueError('无法连接 Audio QC；请确认软件正在运行、已完成启动，且连接文件未过期') from None

    def wait(self, job, session, timeout):
        deadline = time.monotonic() + timeout
        while job['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(0.5)
            job = self.request('/v1/jobs/' + urllib.parse.quote(job['id']) + '?session_id=' + urllib.parse.quote(session))
        return job


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection-file', type=Path, default=discovery_path())
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    flow = sub.add_parser('workflow'); flow.add_argument('--file', choices=['agent_system.md', 'agent_workflow.md', 'advanced_tools.md'])
    tools = sub.add_parser('tools'); tools.add_argument('name', nargs='?')
    session = sub.add_parser('session').add_subparsers(dest='action', required=True)
    opened = session.add_parser('open'); opened.add_argument('--workspace')
    ended = session.add_parser('close'); ended.add_argument('--session', required=True)
    call = sub.add_parser('call'); call.add_argument('name'); call.add_argument('--args', default='{}', help='JSON object, @file, or - for stdin')
    call.add_argument('--session', required=True); call.add_argument('--request-id', default=None)
    call.add_argument('--wait', type=float, default=0, metavar='SECONDS', help='wait briefly, then return job ID; timeout never retries or cancels the operation')
    job = sub.add_parser('job').add_subparsers(dest='action', required=True)
    get = job.add_parser('get'); get.add_argument('id'); get.add_argument('--session', required=True); get.add_argument('--wait', type=float, default=0)
    cancel = job.add_parser('cancel'); cancel.add_argument('id'); cancel.add_argument('--session', required=True)
    args = parser.parse_args(argv)
    try:
        client = Client(args.connection_file)
        if args.command == 'status':
            output = client.request('/v1/status')
        elif args.command == 'workflow':
            output = client.request('/v1/workflow')
            if args.file:
                output = {'version': output['version'], 'source': output['source'], 'name': args.file, 'content': output['files'][args.file]}
            else:
                output = {k: v for k, v in output.items() if k != 'files'}
        elif args.command == 'tools':
            output = client.request('/v1/tools' + ('?name=' + urllib.parse.quote(args.name) if args.name else ''))
            if not args.name:
                output = {'tools': [{k: t[k] for k in ('name', 'description') if k in t} for t in output['tools']]}
        elif args.command == 'session':
            output = client.request('/v1/sessions', {'workspace': args.workspace} if args.workspace else {}) if args.action == 'open' else client.request('/v1/sessions/close', {'session_id': args.session})
        elif args.command == 'call':
            raw = sys.stdin.read() if args.args == '-' else Path(args.args[1:]).read_text(encoding='utf-8') if args.args.startswith('@') else args.args
            values = json.loads(raw)
            if not isinstance(values, dict): raise ValueError('--args 必须为 JSON object')
            request_id = args.request_id or str(uuid.uuid4())
            # Print the ID before submitting: a network timeout may still mean the call was accepted.
            print('request_id=' + request_id, file=sys.stderr)
            output = client.request('/v1/calls', {'session_id': args.session, 'request_id': request_id, 'name': args.name, 'args': values})
            output = client.wait(output, args.session, max(0, args.wait))
        elif args.command == 'job':
            route = '/v1/jobs/' + urllib.parse.quote(args.id)
            output = client.request(route + '/cancel', {'session_id': args.session}) if args.action == 'cancel' else client.wait(client.request(route + '?session_id=' + urllib.parse.quote(args.session)), args.session, max(0, args.wait))
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if output.get('status') == 'failed' or output.get('ok') is False or (isinstance(output.get('result'), dict) and output['result'].get('ok') is False):
            return 1
        return 0
    except (ValueError, KeyError, OSError) as e:
        print(json.dumps({'ok': False, 'message': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
