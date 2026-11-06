"""comfy_client 错误提取自检：ComfyUI 的报错必须能变成人话。跑法：
uv run --no-project --with aiohttp python tests/test_comfy_errors.py（退出码即结论）

背景：原先 4xx 直接 raise_for_status，用户只看到「400, message='Bad Request'」，
图库里的失败理由因此经常是空的或没用；task_error 取不到 exception_message 时
也会返回空白。这里把两种来源都钉住。
"""

import json
import sys

from _loader import load

comfy = load("comfy_client")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        failures.append(f"{name} {detail}")
        print(f"FAIL  {name} {detail}")


# ---------- summarize_error_body：ComfyUI 校验失败 ----------
body = {
    "error": {
        "type": "prompt_outputs_failed_validation",
        "message": "Prompt outputs failed validation",
        "details": "1 个节点校验失败",
    },
    "node_errors": {
        "9": {
            "class_type": "UNETLoader",
            "errors": [{"message": "Value not in list", "details": "unet_name: 'anima-turbo-v1.0.safetensors' not in [...]"}],
        }
    },
}
out = comfy.summarize_error_body(json.dumps(body))
check("body_error_message", "Prompt outputs failed validation" in out, out)
check("body_node_detail", "节点 9" in out and "UNETLoader" in out, out)
check("body_node_message", "Value not in list" in out, out)
check("body_limit", len(comfy.summarize_error_body(json.dumps(body), limit=20)) == 20)

# 只有 error 字符串
out = comfy.summarize_error_body('{"error": "insufficient memory"}')
check("body_error_str", "insufficient memory" in out, out)

# 非 JSON（中转站返回 HTML）
out = comfy.summarize_error_body("<html>502 Bad Gateway</html>")
check("body_plain_text", "502 Bad Gateway" in out, out)

# 空
check("body_empty", "响应体为空" in comfy.summarize_error_body(""))
check("body_none", "响应体为空" in comfy.summarize_error_body(None))

# ---------- task_error：执行期报错 ----------
hist = {
    "status": {
        "status_str": "error",
        "messages": [["execution_error", {
            "exception_message": "CUDA out of memory",
            "node_id": "6",
            "node_type": "KSampler",
        }]],
    }
}
out = comfy.task_error(hist)
check("task_error_message", "CUDA out of memory" in out, str(out))
check("task_error_node", "节点 6/KSampler" in out, str(out))

# exception_message 为空 → 也要给一句
hist_empty = {"status": {"status_str": "error", "messages": [["execution_error", {
    "exception_message": "", "node_id": "3", "node_type": "CLIPTextEncode",
}]]}}
out = comfy.task_error(hist_empty)
check("task_error_empty_msg", "未返回异常信息" in out, str(out))

# 带 node_errors 摘要
hist_ne = {"status": {"status_str": "error", "messages": [["execution_error", {
    "exception_message": "执行失败", "node_id": "4", "node_type": "VAELoader",
    "node_errors": {"4": {"errors": [{"message": "file not found"}]}},
}]]}}
out = comfy.task_error(hist_ne)
check("task_error_node_errors", "file not found" in out, str(out))

# status_str=error 但没有任何 message
out = comfy.task_error({"status": {"status_str": "error", "messages": []}})
check("task_error_status_only", out and "执行失败" in out, str(out))

# 正常任务 → None
check("task_error_none", comfy.task_error({"status": {"status_str": "success", "messages": []}}) is None)
check("task_error_bad_input", comfy.task_error(None) is None)

print("comfy_errors:", "PASS" if not failures else f"FAIL({len(failures)})")
sys.exit(1 if failures else 0)
