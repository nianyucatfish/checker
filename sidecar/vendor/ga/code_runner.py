"""Code executor extracted from GenericAgent ga.py (MIT; see LICENSE).
Checker adaptations: process-group termination, bounded output, no GA runtime header.
"""
import os, sys, re, time, threading, tempfile, subprocess, shutil, signal, codecs
from pathlib import Path
script_dir = str(Path(__file__).parent)

def safe_print(*args, **kwargs):
    try: print(*args, **kwargs)
    except: pass

def code_run(code, code_type="python", timeout=60, cwd=None, code_cwd=None, stop_signal=None, maxlen=10000, myprint=safe_print):
    """代码执行器
    python: 运行复杂的 .py 脚本（文件模式）
    powershell/bash: 运行单行指令（命令模式）
    优先使用python，仅在必要系统操作时使用powershell"""
    cwd = cwd or os.path.join(script_dir, 'temp')
    preview = (code[:60].replace('\n', ' ') + '...') if len(code) > 60 else code.strip()
    yield f"[Action] Running {code_type} in {os.path.basename(cwd)}: {preview}\n"
    cwd = cwd or os.path.join(script_dir, 'temp'); tmp_path = None
    if code_type in ["python", "py"]:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".ai.py", delete=False, mode='w', encoding='utf-8', dir=code_cwd)
        cr_header = os.path.join(script_dir, 'assets', 'code_run_header.py')
        if os.path.exists(cr_header): tmp_file.write(open(cr_header, encoding='utf-8').read())
        tmp_file.write(code)
        tmp_path = tmp_file.name
        tmp_file.close()
        cmd = [sys.executable, "-X", "utf8", "-u", tmp_path]   
    elif code_type in ["powershell", "bash", "sh", "shell", "ps1", "pwsh"]:
        if os.name == 'nt':
            _ps = "pwsh" if shutil.which("pwsh") else "powershell"
            utf8_prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            cmd = [_ps, "-NoProfile", "-NonInteractive", "-Command", utf8_prefix + code]
        else: cmd = ["bash", "-c", code]
    else:
        return {"status": "error", "msg": f"不支持的类型: {code_type}"}
    myprint("code run output:")
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0 # SW_HIDE
    full_stdout = []
    def kill_tree():
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        process.wait()


    def stream_reader(proc, logs):
        try:
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            for line_bytes in iter(lambda: proc.stdout.read(4096), b''):
                line = decoder.decode(line_bytes)
                logs.append(line)
                if sum(map(len, logs)) > maxlen * 2:
                    logs[:] = [smart_format(''.join(logs), maxlen, '\n[output truncated]\n')]
                myprint(line, end="")
            logs.append(decoder.decode(b'', final=True))
        except: pass

    try:
        process = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, cwd=cwd, startupinfo=startupinfo,
            start_new_session=os.name != 'nt',
            creationflags=0x08000000 if os.name == 'nt' else 0
        )
        start_t = time.time()
        t = threading.Thread(target=stream_reader, args=(process, full_stdout), daemon=True)
        t.start()

        istimeout = False
        while process.poll() is None or t.is_alive():
            istimeout = time.time() - start_t > timeout
            if istimeout or stop_signal:
                kill_tree()
                myprint("[Debug] Process killed due to timeout or stop signal.")
                if istimeout: full_stdout.append("\n[Timeout Error] 超时强制终止")
                else: full_stdout.append("\n[Stopped] 用户强制终止")
                break
            time.sleep(0.1)

        t.join(timeout=1)
        exit_code = process.poll()

        stdout_str = "".join(full_stdout)
        status = "success" if exit_code == 0 and not istimeout and not stop_signal else "error"
        status_icon = "✅" if exit_code == 0 else "❌"
        if exit_code is None: status_icon = "⏳" 
        output_snippet = smart_format(stdout_str, max_str_len=600, omit_str='\n\n[omitted long output]\n\n')
        output_snippet = re.sub(r'`{4,}', lambda m: m.group(0)[:3] + '\u200b' + m.group(0)[3:], output_snippet)
        yield f"[Status] {status_icon} Exit Code: {exit_code}\n[Stdout]\n{output_snippet}\n"
        if process.stdout: threading.Thread(target=process.stdout.close, daemon=True).start()
        return {
            "status": status,
            "stdout": smart_format(stdout_str, max_str_len=maxlen, omit_str='\n\n[omitted long output]\n\n'),
            "exit_code": exit_code
        }
    except Exception as e:
        if 'process' in locals(): kill_tree()
        return {"status": "error", "msg": str(e)}
    finally:
        if code_type in ("python", "py") and tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
def smart_format(data, max_str_len=100, omit_str=' ... '):
    if not isinstance(data, str): data = str(data)
    if len(data) < max_str_len + len(omit_str)*2: return data
    return f"{data[:max_str_len//2]}{omit_str}{data[-max_str_len//2:]}"
