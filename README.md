# ssh-agent

دستیار SSH فارسی‌زبان با یک **حلقهٔ ایجنتیک واقعی** (tool-calling چندمرحله‌ای)
و یک **موتور ایمنی صریح**. برخلاف یک تولیدکنندهٔ دستور تک‌شات، این ایجنت
می‌تواند چند دستور تشخیصی پشت‌سرهم اجرا کند، خروجی هرکدام را واقعاً بخواند و
بر اساس آن تصمیم بگیرد — مثلاً بپرسید «چرا nginx بالا نمیاد؟» و ایجنت خودش
`systemctl status` می‌زند، `nginx -t` می‌کند، فایل کانفیگ را می‌خواند و خط
خطادار را پیدا می‌کند.

سازگار با هر endpoint سازگار با OpenAI — خود OpenAI، یا هر سرویس واسط
سازگار با آن. هر کاربر کلید API و آدرس سرویس خودش را در `.env` می‌گذارد.

## منشأ ایده و رعایت کپی‌رایت

این پروژه از ریپوی [specialteam/ai_terminal](https://github.com/specialteam/ai_terminal)
(MIT License) الهام گرفته شده — یک SSH client سبک با PyQt5 و Paramiko که یک
چک‌باکس «AI Mode» ساده داشت (یک فراخوانی تک‌شات به GPT-3.5 برای تولید یک
دستور). ایدهٔ پایه (SSH client گرافیکی + کمک هوش مصنوعی، اتصال با Paramiko،
لاگ JSONL، به‌روزرسانی UI با PyQt signals از یک worker thread) از آنجا گرفته
شده، اما لایهٔ AI به‌طور کامل بازطراحی و از صفر نوشته شده تا به‌جای یک
فراخوانی تک‌شات، یک ایجنت چندمرحله‌ای با ابزار و موتور ایمنی باشد. جزئیات
کامل اینکه چه چیزی الهام‌گرفته و چه چیزی کاملاً جدید است در
[NOTICE.md](NOTICE.md) آمده. این پروژه هم طبق شرط MIT License با همان
لایسنس منتشر می‌شود — [LICENSE](LICENSE).

## راه‌اندازی

### پیش‌نیازها

| نیاز | توضیح |
|---|---|
| Python 3.12+ | همراه با [uv](https://github.com/astral-sh/uv) برای مدیریت وابستگی‌ها |
| کلید API | هر endpoint سازگار با OpenAI — خود OpenAI یا هر سرویس سازگار دیگر |
| یک سرور SSH | سرور واقعی خودتان، یا سرور تمرینی داخل Docker (بخش بعد) |

### قدم ۱ — نصب وابستگی‌ها

```bash
uv sync                    # هستهٔ پروژه (کافی برای حالت CLI)
uv sync --extra gui        # + رابط گرافیکی PyQt5 (حدود ۱۵۰ مگابایت دانلود)
```

### قدم ۲ — گذاشتن کلید API

```bash
cp .env.example .env
```

سپس `.env` را باز کنید و کلید خودتان را بگذارید:

```
LLM_API_KEY=کلید-واقعی-شما
LLM_BASE_URL=                      # خالی برای OpenAI؛ یا آدرس endpoint سازگار شما
LLM_MODEL=gpt-4o
```

فایل `.env` در `.gitignore` هست و هرگز کامیت نمی‌شود. بدون این کلید، برنامه
هنگام اجرا با پیام روشن `LLM_API_KEY is not set` متوقف می‌شود.

### قدم ۳ — بالا آوردن یک سرور تمرینی (توصیه‌شده برای اولین اجرا)

پیش از وصل کردن ایجنت به سرور واقعی، آن را روی یک کانتینر یک‌بارمصرف امتحان
کنید. ایمیج شامل Ubuntu + sshd + nginx است:

```bash
docker build -t ssh-agent-testbox tests/docker
docker run -d --name ssh-agent-test -p 2222:22 ssh-agent-testbox
```

مشخصات اتصال: `127.0.0.1:2222`، کاربر `agentuser`، رمز `agentpass`.

برای اینکه ایجنت واقعاً چیزی برای پیدا کردن داشته باشد، می‌توانید عمداً یک
خرابی بسازید:

```bash
docker exec ssh-agent-test /opt/break_nginx.sh   # کانفیگ nginx را خراب و سرویس را متوقف می‌کند
docker exec ssh-agent-test /opt/fill_disk.sh     # یک فایل ۵۰۰ مگابایتی در /var/log/bigapp می‌سازد
```

> **کاربران Git Bash روی ویندوز:** Git Bash مسیر `/opt/...` را به مسیر ویندوزی
> ترجمه می‌کند و دستور بالا با خطای `no such file or directory` شکست می‌خورد.
> جلوی دستور `MSYS_NO_PATHCONV=1` بگذارید، یا از PowerShell استفاده کنید.

بعد از اجرای `break_nginx.sh`، سرویس واقعاً خراب است و ایجنت باید خودش
پیدایش کند:

```
nginx: [emerg] unknown directive "this_is_not_a_directive" in /etc/nginx/nginx.conf:12
```

پاک کردن کانتینر در پایان:

```bash
docker rm -f ssh-agent-test
```

### قدم ۴ — اجرا

#### حالت CLI

```bash
# روی سرور تمرینی
uv run python cli.py --host 127.0.0.1 --port 2222 --user agentuser --password agentpass

# روی سرور واقعی، با کلید SSH
uv run python cli.py --host YOUR_HOST --user YOUR_USER --key ~/.ssh/id_rsa
```

اگر نه `--password` بدهید و نه `--key`، رمز به‌صورت امن از شما پرسیده می‌شود.

بعد از اتصال، هدف خود را به فارسی بنویسید — مثلاً:

```
شما> چرا nginx بالا نمیاد؟
شما> چی داره دیسک رو پر می‌کنه؟
```

خروجی با [rich](https://github.com/Textualize/rich) رندر می‌شود: یک اسپینر
زنده در حین کار، برای هر دستور یک پنل رنگی بر اساس سطح ایمنی، و در پایان یک
پنل پاسخ همراه با متریک‌های آن run (تعداد ابزار، فراخوانی مدل، توکن، زمان):

```
┌────────────────────── [SAFE] $ systemctl status nginx ──────────────────────┐
│ exit_code=3                                                                 │
│ nginx.service: Failed with result 'exit-code'                               │
└─────────────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────── ایجنت ───────────────────────────────────┐
│ خط ۱۲ فایل کانفیگ سمی‌کالن ندارد.                                           │
└───────────────── 3 ابزار · 4 فراخوانی مدل · 2140 توکن · 6.2s ───────────────┘
```

خروج با `exit` یا `quit` یا `خروج`.

#### حالت GUI

```bash
uv run python gui.py --host 127.0.0.1 --port 2222 --user agentuser --password agentpass
```

پنجره به دو بخش تقسیم می‌شود: چپ = ترمینال زنده، راست = چت با ایجنت. هر
دستوری که ایجنت اجرا می‌کند به‌صورت یک کارت رنگی (SAFE سبز / CONFIRM زرد /
BLOCKED قرمز) با دلیل اجرا و خروجی نمایش داده می‌شود.

### ادامه دادن یک گفتگوی قبلی

تاریخچهٔ هر سشن در `.ssh_agent/sessions/` ذخیره می‌شود:

```bash
uv run python cli.py --list-sessions                            # فهرست سشن‌های ذخیره‌شده
uv run python cli.py --host ... --user ... --session my-debug   # ادامه یا ساخت سشن با این نام
uv run python cli.py --host ... --user ... --no-store           # بدون ذخیره روی دیسک
```

`--session` در حالت GUI هم کار می‌کند.

### همهٔ گزینه‌های خط فرمان

| گزینه | پیش‌فرض | توضیح |
|---|---|---|
| `--host` | — | آدرس سرور (الزامی) |
| `--port` | `22` | پورت SSH |
| `--user` | — | نام کاربری (الزامی) |
| `--password` | پرسیده می‌شود | رمز عبور |
| `--key` | — | مسیر فایل کلید خصوصی SSH |
| `--session` | سشن جدید | شناسهٔ سشن برای ادامهٔ گفتگو |
| `--list-sessions` | — | نمایش سشن‌های ذخیره‌شده و خروج |
| `--no-store` | — | تاریخچه روی دیسک ذخیره نشود |
| `--log` | `session_log.jsonl` | مسیر فایل لاگ JSONL |

### وقتی چیزی کار نکرد

| نشانه | علت و راه‌حل |
|---|---|
| `LLM_API_KEY is not set` | فایل `.env` ساخته نشده یا کلید داخلش خالی است |
| پنل قرمز «خطا در تماس با مدل» | کلید نامعتبر، اعتبار تمام‌شده، یا `LLM_BASE_URL` اشتباه |
| `AuthenticationException` | نام کاربری، رمز یا کلید SSH اشتباه است |
| `ModuleNotFoundError: PyQt5` | `uv sync --extra gui` را اجرا کنید |
| خروجی فارسی به‌هم‌ریخته در ویندوز | ترمینال را روی UTF-8 بگذارید (`chcp 65001`) |

## موتور ایمنی

هر دستوری که مدل می‌خواهد اجرا کند از `agent/policy.py` عبور می‌کند:

| سطح | مثال | رفتار |
|---|---|---|
| `SAFE` | `ls`, `cat`, `systemctl status`, `df` | اجرای خودکار |
| `CONFIRM` | `apt install`, `systemctl restart`, ویرایش فایل | نیاز به تأیید کاربر |
| `BLOCKED` | `rm -rf /`, `mkfs`, fork bomb, `dd of=/dev/sd*` | رد قطعی، هرگز اجرا نمی‌شود |

## معماری

```
ssh-agent/
  core/ssh_session.py     # سشن پایدار Paramiko (cd/env بین دستورات حفظ می‌شود)
  core/logger.py          # لاگ JSONL هر ورودی/tool call/پاسخ
  agent/policy.py         # طبقه‌بندی SAFE / CONFIRM / BLOCKED
  agent/toolkit.py        # ساخت خودکار JSON schema از type hint و docstring
  agent/tools.py          # SSHToolkit: run_command, read_file, system_facts, tail_log
  agent/events.py         # رویدادهای typed یک run + متریک‌ها
  agent/session.py        # ذخیره و ادامهٔ تاریخچهٔ گفتگو
  agent/loop.py           # حلقهٔ tool-calling به‌صورت جریانی از رویدادها
  agent/prompts.py        # system prompt فارسی/دوزبانه + نرمال‌سازی ورودی فارسی
  ui/main_window.py       # PyQt5 — ترمینال + چت + کارت‌های tool call
  cli.py / gui.py         # نقطهٔ ورود بدون‌GUI و با GUI
```

### الهام از agno

معماری داخلی با نگاه به [agno-agi/agno](https://github.com/agno-agi/agno)
بازنویسی شده — سه الگویی که از آنجا گرفته شده:

**۱. Toolkit به‌جای schema دستی.** در agno یک ابزار فقط یک متد پایتونیِ
type-hint‌دار با docstring است و schema از روی امضای تابع ساخته می‌شود. اینجا
هم `agent/toolkit.py` همین کار را می‌کند؛ قبلاً یک جدول `TOOL_SCHEMAS` دستی
کنار پیاده‌سازی‌ها بود که باید هم‌زمان به‌روز نگه داشته می‌شد:

```python
@tool
def tail_log(self, unit_or_path: str, lines: int = 100) -> str:
    """Show the last N lines of a systemd unit's journal or a log file.

    Args:
        unit_or_path: A systemd unit name (e.g. "nginx") or a file path.
        lines: Number of lines to show from the end.
    """
```

**۲. جریان رویدادهای typed.** مثل `RunResponse` جریانی در agno،
`Agent.run()` یک generator از رویدادها است (`RunStarted`، `ToolCallStarted`،
`ToolCallCompleted`، `AgentContent`، `RunCompleted`، `RunError`). هر دو
فرانت‌اند همین یک جریان را مصرف می‌کنند؛ پیش از این رابط PyQt مجبور بود
متد `executor.call` را monkey-patch کند تا بفهمد یک دستور کِی شروع شد.

**۳. سشن و متریک.** مثل `Agent(session_id=..., storage=...)` در agno،
تاریخچهٔ گفتگو در `.ssh_agent/sessions/` ذخیره می‌شود و با `--session` ادامه
پیدا می‌کند، و هر run توکن مصرفی و زمان خود را گزارش می‌دهد.

نکته: هیچ کدی از agno کپی نشده و agno وابستگی این پروژه نیست — فقط الگوهای
طراحی‌اش الهام‌بخش بوده است.

## تست

```bash
uv run pytest                        # policy + toolkit + session + loop + gui (بدون شبکه)
uv run pytest tests/test_integration_docker.py   # end-to-end روی کانتینر واقعی
```

تست‌های integration به یک کانتینر Docker هدف نیاز دارند (بدون نیاز به هیچ
سرور واقعی):

```bash
docker build -t ssh-agent-testbox tests/docker
docker run -d --name ssh-agent-test -p 2222:22 ssh-agent-testbox
uv run pytest tests/test_integration_docker.py -v
```

تست‌های `tests/test_gui.py` رابط PyQt را به‌صورت offscreen اجرا می‌کنند (بدون
نمایشگر، بدون سرور SSH) و اگر extra `gui` نصب نباشد خودکار skip می‌شوند.

تست‌های `tests/test_loop.py` کل حلقهٔ ایجنت را با یک کلاینت مدل ساختگی
اجرا می‌کنند، پس مسیر رویدادها، متریک‌ها، ذخیرهٔ سشن و رفتار BLOCKED/CONFIRM
بدون کلید API و بدون شبکه پوشش داده می‌شود.

این تست‌ها کل مسیر SSH → policy → ابزارها را روی سرور واقعی امتحان می‌کنند
(از جمله سناریوهای «nginx خراب» و «دیسک پر» از پلن پروژه)، اما لایهٔ فراخوانی
مدل زبانی را شامل نمی‌شوند چون به یک `LLM_API_KEY` واقعی نیاز دارد.

## محدودیت‌های شناخته‌شده

- سناریوی end-to-end با مدل زبانی واقعی (نه فقط ابزارها) نیاز به یک کلید API
  معتبر دارد و در این مخزن به‌صورت خودکار اجرا نشده است.
- موتور ایمنی مبتنی بر الگوهای regex است، نه sandbox واقعی؛ برای محیط production
  توصیه می‌شود کاربر SSH با کمترین دسترسی ممکن (sudo محدود) استفاده شود.
