"""Save the currently open Photoroom collection through Chrome's native UI.

No cookies, remote debugging, private endpoints, or browser-profile copying.
"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import signal
import struct
import time
from urllib.parse import urlparse, unquote

STOP = False

def stop(*_):
    global STOP
    STOP = True


def check_stop():
    if STOP:
        raise KeyboardInterrupt


def safe_name(label):
    return re.sub(r'[\x00-\x1f/:\\]', '_', label).strip('. ')[:110] or 'image'


def png_size(path):
    with Path(path).open('rb') as stream:
        head = stream.read(24)
    if len(head) != 24 or head[:8] != b'\x89PNG\r\n\x1a\n' or head[12:16] != b'IHDR':
        raise ValueError('הקובץ שהתקבל אינו PNG תקין')
    return struct.unpack('>II', head[16:24])


class Chrome:
    def __init__(self):
        import ApplicationServices as AX
        import Quartz as Q
        import AppKit
        import objc
        self.AX, self.Q, self.AppKit, self.objc = AX, Q, AppKit, objc
        if not AX.AXIsProcessTrusted():
            raise RuntimeError('נדרשת הרשאת נגישות ל-Terminal: הגדרות המערכת ← פרטיות ואבטחה ← נגישות. הפעילו Terminal ואז הפעילו שוב את האפליקציה. ההרשאה מאפשרת לאפליקציה ללחוץ ולכתוב בחלונות.')
        apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_('com.google.Chrome')
        if not apps:
            raise RuntimeError('פתחו Chrome ואת האוסף ב-Photoroom לפני ההפעלה.')
        self.running = apps[0]
        self.app = AX.AXUIElementCreateApplication(self.running.processIdentifier())
        AX.AXUIElementSetMessagingTimeout(self.app, 2.0)
        # Chromium may lazily expose its web accessibility tree.
        AX.AXUIElementSetAttributeValue(self.app, 'AXEnhancedUserInterface', True)
        self.lib = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.lib.AXValueGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.lib.AXValueGetValue.restype = ctypes.c_bool

    def attr(self, element, name, default=None):
        err, value = self.AX.AXUIElementCopyAttributeValue(element, name, None)
        return value if err == 0 and value is not None else default

    def walk(self, element, depth=0):
        check_stop()
        if depth > 35:
            return
        yield element
        for child in self.attr(element, 'AXChildren', []):
            yield from self.walk(child, depth + 1)

    def label(self, element):
        for key in ('AXTitle', 'AXDescription', 'AXHelp'):
            value = self.attr(element, key)
            if isinstance(value, str) and value:
                return value
        return ''

    def frame(self, element):
        values = []
        for key, kind in [('AXPosition', 1), ('AXSize', 2)]:
            value = self.attr(element, key)
            if value is None:
                return None
            pair = (ctypes.c_double * 2)()
            pointer = self.objc.pyobjc_id(value)
            if not self.lib.AXValueGetValue(pointer, kind, ctypes.byref(pair)):
                return None
            values.extend(pair)
        return tuple(values)

    def front_guard(self):
        check_stop()
        front = self.AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front.bundleIdentifier() != 'com.google.Chrome':
            raise RuntimeError('הפעולה נעצרה כי Chrome אינו החלון הפעיל. הפעילו שוב כדי להמשיך.')
        point = self.Q.CGEventGetLocation(self.Q.CGEventCreate(None))
        if point.x < 5 and point.y < 5:
            raise KeyboardInterrupt

    def web(self):
        window = self.attr(self.app, 'AXFocusedWindow')
        if window is None:
            raise RuntimeError('לא נמצא חלון Chrome פעיל')
        for node in self.walk(window):
            if self.attr(node, 'AXRole') == 'AXWebArea':
                url = str(self.attr(node, 'AXURL', ''))
                if urlparse(url).hostname == 'app.photoroom.com' and urlparse(url).path.startswith('/batch'):
                    return node
                raise RuntimeError('בחרו את לשונית האוסף בכתובת app.photoroom.com/batch.')
        raise RuntimeError('לא נמצא עמוד Photoroom. סגרו תפריטים וחלונות שמירה ונסו שוב.')

    def collection(self):
        web = self.web()
        nodes = list(self.walk(web))
        candidates = []
        for node in nodes:
            kids = self.attr(node, 'AXChildren', [])
            buttons = [c for c in kids if self.attr(c, 'AXRole') == 'AXButton' and self.label(c)]
            # Photoroom's fullscreen filmstrip is a content list of named buttons.
            if buttons and len(buttons) == len(kids) and self.attr(node, 'AXRole') in ('AXList', 'AXGroup'):
                labels = [self.label(c) for c in buttons]
                matches = [n for n in nodes if self.attr(n, 'AXRole') == 'AXImage' and self.label(n) in labels]
                if matches:
                    candidates.append((node, buttons, matches))
        if len(candidates) != 1:
            raise RuntimeError('פתחו תמונה באוסף במצב מוגדל, כך שהתמונות הממוזערות מוצגות למטה. לא זוהתה רשימת תמונות יחידה.')
        strip, buttons, images = candidates[0]
        labels = [self.label(b) for b in buttons]
        if len(labels) != len(set(labels)):
            raise RuntimeError('יש תמונות עם שמות כפולים. נדרשים שמות ייחודיים כדי למנוע דילוג שגוי.')
        return strip, buttons, images

    def press(self, element):
        self.front_guard()
        err = self.AX.AXUIElementPerformAction(element, 'AXPress')
        if err:
            self.click(element)

    def click(self, element, right=False):
        self.front_guard()
        frame = self.frame(element)
        if not frame or frame[2] <= 0 or frame[3] <= 0:
            raise RuntimeError('הרכיב אינו נראה על המסך')
        x, y, w, h = frame
        point = (x + w / 2, y + h / 2)
        q = self.Q
        button = q.kCGMouseButtonRight if right else q.kCGMouseButtonLeft
        for kind in ([q.kCGEventRightMouseDown, q.kCGEventRightMouseUp] if right else [q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp]):
            q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, kind, point, button))

    def key(self, code, flags=0):
        self.front_guard()
        for down in [True, False]:
            event = self.Q.CGEventCreateKeyboardEvent(None, code, down)
            self.Q.CGEventSetFlags(event, flags)
            self.Q.CGEventPost(self.Q.kCGHIDEventTap, event)

    def type_text(self, text):
        self.front_guard()
        for down in [True, False]:
            event = self.Q.CGEventCreateKeyboardEvent(None, 0, down)
            self.Q.CGEventKeyboardSetUnicodeString(event, len(text.encode('utf-16-le')) // 2, text)
            self.Q.CGEventPost(self.Q.kCGHIDEventTap, event)

    def scroll(self, direction):
        strip, _, _ = self.collection()
        self.front_guard()
        x, y, w, h = self.frame(strip)
        q = self.Q
        point = (x + w / 2, y + h / 2)
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, point, q.kCGMouseButtonLeft))
        event = q.CGEventCreateScrollWheelEvent(None, q.kCGScrollEventUnitPixel, 2, 0, int(direction * max(250, w * .65)))
        q.CGEventSetLocation(event, point)
        q.CGEventPost(q.kCGHIDEventTap, event)
        time.sleep(.8)

    def signature(self):
        _, buttons, _ = self.collection()
        return tuple((self.label(b), self.frame(b)) for b in buttons)

    def rewind(self):
        stable = 0
        previous = self.signature()
        for _ in range(150):
            self.scroll(1)
            current = self.signature()
            stable = stable + 1 if current == previous else 0
            if stable >= 3:
                return
            previous = current
        raise RuntimeError('לא ניתן לוודא שהגענו לתחילת האוסף; הפעולה נעצרה.')

    def find(self, predicate, timeout=12):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            check_stop()
            for element in self.walk(self.app):
                if predicate(element):
                    return element
            time.sleep(.25)
        raise RuntimeError('האתר או חלון השמירה לא הגיבו בזמן. ההתקדמות נשמרה.')

    def select_downloads(self, target):
        """Force the native save panel to Downloads.

        macOS does not expose the save panel's current folder reliably through
        Accessibility. Select Downloads explicitly, then verify the real result
        after Save by checking the exact target path on disk.
        """
        destination = Path.home() / 'Downloads'
        if target.parent.resolve() != destination.resolve():
            raise RuntimeError('האפליקציה שומרת רק בתיקיית Downloads של המשתמש.')
        self.find(lambda e: self.attr(e, 'AXIdentifier') == 'saveAsNameTextField')
        self.key(37, self.Q.kCGEventFlagMaskCommand | self.Q.kCGEventFlagMaskAlternate)
        time.sleep(.5)

    def save(self, label, target, settle):
        _, buttons, _ = self.collection()
        selected = next((b for b in buttons if self.label(b) == label), None)
        if selected is None:
            raise RuntimeError('התמונה נעלמה מהרשימה; הפעולה נעצרה')
        self.press(selected)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            _, _, images = self.collection()
            current = next((i for i in images if self.label(i) == label), None)
            if current is not None:
                break
            time.sleep(.2)
        else:
            raise RuntimeError('התמונה הנבחרת לא נטענה')
        time.sleep(settle)
        # Re-read after rendering; never keep stale image references across selection.
        _, _, images = self.collection()
        current = next(i for i in images if self.label(i) == label)
        self.click(current, right=True)
        item = self.find(lambda e: self.attr(e, 'AXRole') == 'AXMenuItem' and self.label(e).replace('…', '').replace('...', '').strip() == 'Save Image As')
        self.press(item)
        self.select_downloads(target)
        field = self.find(lambda e: self.attr(e, 'AXIdentifier') == 'saveAsNameTextField')
        self.click(field)
        self.key(0, self.Q.kCGEventFlagMaskCommand)
        self.type_text(target.name)
        time.sleep(.2)
        actual = str(self.attr(field, 'AXValue', '')).strip()
        # Native macOS save panels may hide or normalize the extension in the
        # visible filename field. The real safety check is the exact target
        # file that must appear in Downloads after Save.
        acceptable_names = {target.name, target.stem}
        if actual and actual not in acceptable_names:
            raise RuntimeError(f'שם הקובץ בחלון השמירה אינו תואם לשם המבוקש: {actual!r}. הפעולה נעצרה.')
        if target.exists():
            raise RuntimeError('קובץ היעד כבר קיים. הפעולה נעצרה בלי לדרוס אותו.')
        button = self.find(lambda e: self.attr(e, 'AXIdentifier') == 'OKButton' and self.label(e) == 'Save')
        self.press(button)
        end = time.monotonic() + 45
        previous = None
        stable = 0
        while time.monotonic() < end:
            check_stop()
            if target.exists():
                size = target.stat().st_size
                stable = stable + 1 if size == previous and size > 24 else 0
                if stable >= 3:
                    return png_size(target)
                previous = size
            time.sleep(.4)
        raise RuntimeError('לא ניתן לאמת את שמירת הקובץ; הפעולה נעצרה.')


def main():
    parser = argparse.ArgumentParser(description='Photoroom → Downloads, using the existing Chrome window')
    parser.add_argument('--session', default='collection', help='Unique name per collection; reuse it to resume')
    parser.add_argument('--settle', type=float, default=2.0)
    parser.add_argument('--limit', type=int, default=0, help='Optional limit for a test run')
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    destination = Path.home() / 'Downloads'
    session = safe_name(args.session)
    state_path = destination / f'photoroom-{session}-progress.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'saved': {}}
    browser = Chrome()
    print('בחרו עכשיו את לשונית Photoroom במצב תמונה מוגדלת. ההפעלה תתחיל בעוד 7 שניות.', flush=True)
    print('לעצירה: העבירו את העכבר לפינה השמאלית העליונה, או עברו לחלון אחר. אין להשתמש בעכבר ובמקלדת בזמן השמירה.', flush=True)
    time.sleep(7)
    browser.front_guard()
    strip, buttons, images = browser.collection()
    if args.inspect:
        print(json.dumps({'names': [browser.label(b) for b in buttons], 'strip': browser.frame(strip), 'current': [browser.label(i) for i in images]}, ensure_ascii=False, indent=2))
        return
    browser.rewind()
    stable, saved_now = 0, 0
    encountered = set()
    previous = None
    for page in range(1000):
        browser.front_guard()
        _, buttons, _ = browser.collection()
        names = [browser.label(b) for b in buttons]
        encountered.update(names)
        for label in names:
            check_stop()
            entry = state['saved'].get(label)
            if entry:
                existing = destination / Path(entry['file']).name
                if existing.exists() and hashlib.sha256(existing.read_bytes()).hexdigest() == entry['sha256']:
                    continue
                raise RuntimeError(f'קובץ שנשמר בעבר חסר או השתנה: {existing.name}. בחרו שם הרצה חדש.')
            suffix = hashlib.sha256(label.encode()).hexdigest()[:8]
            target = destination / f'photoroom-{session}-{safe_name(label)}-{suffix}.png'
            if target.exists():
                raise RuntimeError(f'קובץ קיים ללא אישור שמירה ביומן: {target.name}. בחרו שם הרצה חדש.')
            print(f'שומר: {label}', flush=True)
            dimensions = browser.save(label, target, max(.5, args.settle))
            state['saved'][label] = {'file': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'dimensions': dimensions}
            temporary = state_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            temporary.replace(state_path)
            saved_now += 1
            print(f'נשמרו בהרצה זו {saved_now} תמונות; {dimensions[0]}×{dimensions[1]}', flush=True)
            if args.limit and saved_now >= args.limit:
                print('הגענו למגבלת הבדיקה. אפשר להריץ שוב להמשך.', flush=True)
                return
        signature = browser.signature()
        browser.scroll(-1)
        after = browser.signature()
        stable = stable + 1 if after == signature and after == previous else 0
        previous = after
        if stable >= 4:
            print(f'הגלילה אינה חושפת תמונות נוספות. אומתה שמירת {len(encountered)} תמונות שזוהו באוסף. בדקו שהמספר תואם לאוסף באתר.', flush=True)
            print(f'הקבצים נשמרו ב־{destination}', flush=True)
            return
    raise RuntimeError('הגענו למגבלת גלילה. ההתקדמות נשמרה; לא הוכרז סיום האוסף.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nנעצר. הקבצים שכבר נשמרו נשארים ב-Downloads.', flush=True)
    except Exception as exc:
        print(f'\nהפעולה נעצרה: {exc}', flush=True)
        raise SystemExit(1)
