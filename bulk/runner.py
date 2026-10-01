"""Sequential uploads through Photoroom's public background-remover UI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time

URL = 'https://www.photoroom.com/tools/background-remover'
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def collect(paths, output):
    output = output.resolve()
    found = set()
    for value in paths:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise ValueError(f'לא נמצא: {path}')
        for item in (path.rglob('*') if path.is_dir() else [path]):
            item = item.resolve()
            if item.is_file() and item.suffix.lower() in EXTENSIONS:
                if output != item and output not in item.parents:
                    found.add(item)
    return sorted(found, key=lambda p: str(p).casefold())


def identity(source):
    return hashlib.sha256((str(source.resolve()) + '\0' + digest(source)).encode()).hexdigest()


def output_name(source, key):
    stem = re.sub(r'[\x00-\x1f/:\\]', '_', source.stem).strip('. ')[:70] or 'image'
    return f'{stem}-no-bg-{key[:16]}.png'


class Journal:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'progress.json'
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {'version': 1, 'saved': {}}
        if self.data.get('version') != 1 or not isinstance(self.data.get('saved'), dict):
            raise ValueError('יומן ההתקדמות אינו תקין. בחרו תיקיית פלט אחרת.')

    def write(self):
        temp = self.path.with_suffix('.json.tmp')
        with temp.open('w') as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)

    def verified(self, key):
        entry = self.data['saved'].get(key)
        if not entry:
            return False
        target = self.folder / Path(entry['file']).name
        if not target.is_file() or digest(target) != entry['sha256']:
            raise RuntimeError(f'קובץ שמור חסר או השתנה: {target.name}. בחרו תיקיית פלט חדשה.')
        return True

    def save(self, source, key, download):
        from PIL import Image
        target = self.folder / output_name(source, key)
        if target.exists():
            raise RuntimeError(f'קובץ קיים ללא רישום מאומת: {target.name}. אין דריסה; בחרו תיקיית פלט חדשה.')
        raw = self.folder / f'.{key}.download'
        pending = self.folder / f'.{key}.png.tmp'
        try:
            download.save_as(str(raw))
            with Image.open(raw) as image:
                image.load()  # Decode the full image, not only its header.
                if image.format not in {'PNG', 'JPEG', 'WEBP'}:
                    raise ValueError('ההורדה אינה תמונה נתמכת')
                rgba = image.convert('RGBA')
                dimensions = list(rgba.size)
                transparent = rgba.getextrema()[3][0] < 255
                rgba.save(pending, format='PNG')
            # Hard-link creation is atomic and refuses to replace an existing file.
            os.link(pending, target)
            self.data['saved'][key] = {
                'source': str(source), 'file': target.name,
                'sha256': digest(target), 'dimensions': dimensions,
                'transparent': transparent,
            }
            self.write()
            return target, dimensions, transparent
        finally:
            raw.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)


def process_page(page, source, timeout):
    """One fresh page per source prevents downloading a previous result."""
    page.goto(URL, wait_until='domcontentloaded', timeout=timeout)
    with page.expect_file_chooser(timeout=timeout) as chooser:
        page.get_by_role('button', name='Start from a photo', exact=True).click(timeout=timeout)
    chooser.value.set_files(str(source), timeout=timeout)
    result = page.get_by_role('img', name='Image with background removed', exact=True).first
    result.wait_for(state='visible', timeout=timeout)
    with page.expect_download(timeout=timeout) as event:
        page.get_by_role('button', name='Download', exact=True).click(timeout=timeout)
    return event.value


def choose_inputs():
    import AppKit
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    app.activateIgnoringOtherApps_(True)
    panel = AppKit.NSOpenPanel.openPanel()
    panel.setTitle_('בחרו תמונות או תיקייה — אפשר לבחור 90 קבצים ויותר')
    panel.setMessage_('התמונות שנבחרו יועלו ל-Photoroom להסרת רקע, אחת אחרי השנייה.')
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(True)
    panel.setAllowsMultipleSelection_(True)
    return [str(url.path()) for url in panel.URLs()] if panel.runModal() == AppKit.NSModalResponseOK else []


def run(args):
    import fcntl
    from playwright.sync_api import sync_playwright
    paths = args.paths or choose_inputs()
    if not paths:
        print('לא נבחרו קבצים.')
        return
    output = args.output.expanduser().resolve()
    sources = collect(paths, output)
    if not sources:
        raise ValueError('לא נמצאו תמונות PNG, JPG או WEBP בבחירה.')
    output.mkdir(parents=True, exist_ok=True)
    # Keep the descriptor alive throughout the run; the OS releases it on exit.
    with (output / '.run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('כבר פועלת שמירה לתיקייה זו.')
        journal = Journal(output)
        queue = [(p, identity(p)) for p in sources]
        pending = [(p, k) for p, k in queue if not journal.verified(k)]
        skipped = len(queue) - len(pending)
        if args.limit:
            pending = pending[:args.limit]
        print(f'נבחרו {len(queue)} תמונות. כבר נשמרו: {skipped}. לעיבוד כעת: {len(pending)}.', flush=True)
        print(f'תיקיית תוצאות: {output}', flush=True)
        if not pending:
            return
        print('לעצירה: Control+C בחלון זה. בהרצה חוזרת תתבצע המשך שמירה.', flush=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=False)
            context = browser.new_context(accept_downloads=True, locale='en-US')
            try:
                for index, (source, key) in enumerate(pending, 1):
                    if identity(source) != key:
                        raise RuntimeError(f'קובץ המקור השתנה במהלך ההרצה: {source.name}')
                    print(f'[{index}/{len(pending)}] מעלה ומעבד: {source.name}', flush=True)
                    page = context.new_page()
                    try:
                        download = process_page(page, source, int(args.timeout * 1000))
                        target, size, transparent = journal.save(source, key, download)
                        print(f'נשמר: {target.name} ({size[0]}×{size[1]})', flush=True)
                        if not transparent:
                            print('שימו לב: בתוצאה זו לא זוהו פיקסלים שקופים. בדקו את התמונה.', flush=True)
                    except Exception as exc:
                        print(f'נעצר בקובץ {source.name}: {exc}', flush=True)
                        print('לא ממשיכים לקובץ הבא. בדקו אם האתר מציג שגיאה, התחברות או מגבלת שימוש.', flush=True)
                        if not args.non_interactive:
                            input('הדפדפן נשאר פתוח לבדיקה. לחצו Enter לסיום ואז הפעילו שוב להמשך: ')
                        raise
                    finally:
                        page.close()
                    if index < len(pending):
                        time.sleep(args.delay)
            finally:
                context.close()
                browser.close()
        print(f'הושלם: נשמרו ואומתו {len(pending)} תמונות חדשות; {skipped} כבר היו שמורות.', flush=True)


def main():
    parser = argparse.ArgumentParser(description='העלאה ושמירה סדרתית דרך כלי הסרת הרקע של Photoroom')
    parser.add_argument('paths', nargs='*', help='קבצים או תיקיות; ללא פרמטר נפתח חלון בחירה')
    parser.add_argument('--output', type=Path, default=Path.home() / 'Downloads' / 'Photoroom-Results')
    parser.add_argument('--limit', type=int, default=0, help='בדיקה מדגמית; 0 = כל הקבצים')
    parser.add_argument('--delay', type=float, default=3, help='המתנה בין תמונות, בשניות')
    parser.add_argument('--timeout', type=float, default=120, help='זמן המתנה לכל שלב באתר')
    parser.add_argument('--non-interactive', action='store_true', help='יציאה מיד לאחר שגיאה')
    args = parser.parse_args()
    if args.limit < 0 or args.delay < 0 or args.timeout <= 0:
        parser.error('ערכי limit ו-delay חייבים להיות אפס ומעלה, ו-timeout גדול מאפס')
    try:
        run(args)
    except KeyboardInterrupt:
        print('\nנעצר. תמונות שכבר נשמרו נשארות בתיקיית התוצאות.')
        return 130
    except Exception as exc:
        print(f'\nהפעולה לא הושלמה: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
