"""Save the currently open Photoroom collection through Chrome's native UI.

No cookies, remote debugging, private endpoints, or browser-profile copying.
"""
import argparse
import ctypes
from pathlib import Path
import re
import signal
import struct
import time
from urllib.parse import urlparse

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
        self.running.activateWithOptions_(self.AppKit.NSApplicationActivateIgnoringOtherApps)
        time.sleep(.25)
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
        # Do not stop merely because focus temporarily leaves Chrome or because
        # the pointer reaches a screen corner. Explicit Control+C remains the
        # supported user-initiated stop mechanism.
        check_stop()

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

    def select_downloads(self):
        """Force Chrome's native save panel to Downloads."""
        self.find(lambda e: self.attr(e, 'AXIdentifier') == 'saveAsNameTextField')
        self.key(37, self.Q.kCGEventFlagMaskCommand | self.Q.kCGEventFlagMaskAlternate)
        time.sleep(.15)

    def download_snapshot(self, destination):
        """Return regular files currently present in Downloads."""
        result = {}
        for path in destination.iterdir():
            try:
                if path.is_file() and not path.name.endswith(('.crdownload', '.download')):
                    stat = path.stat()
                    result[path.name] = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                pass
        return result

    def wait_for_new_download(self, destination, before, default_name, timeout=20):
        """Identify the file Chrome actually created, independent of extension display."""
        deadline = time.monotonic() + timeout
        candidate = None
        previous_size = None
        stable = 0
        default_stem = Path(default_name).stem.casefold()
        while time.monotonic() < deadline:
            check_stop()
            current = self.download_snapshot(destination)
            changed = []
            for name, meta in current.items():
                if name not in before or before[name] != meta:
                    path = destination / name
                    if name.endswith(('.crdownload', '.download')):
                        continue
                    changed.append(path)
            # Prefer a name matching Chrome's proposed name/stem. If exactly one
            # file changed, accept it even when macOS hid or normalized the extension.
            matching = [p for p in changed if p.name.casefold() == default_name.casefold()
                        or p.stem.casefold() == default_stem]
            choices = matching or (changed if len(changed) == 1 else [])
            if len(choices) == 1:
                path = choices[0]
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    time.sleep(.25)
                    continue
                if candidate == path and size == previous_size and size > 24:
                    stable += 1
                else:
                    candidate, previous_size, stable = path, size, 0
                if stable >= 1:
                    return path
            time.sleep(.12)
        raise RuntimeError('לא ניתן לזהות ולאמת את הקובץ החדש שנשמר ב-Downloads.')

    def save(self, label, destination, settle):
        _, buttons, images = self.collection()
        current = next((i for i in images if self.label(i) == label), None)
        if current is None:
            selected = next((b for b in buttons if self.label(b) == label), None)
            if selected is None:
                raise RuntimeError('התמונה נעלמה מהרשימה; הפעולה נעצרה')
            self.press(selected)
            end = time.monotonic() + 5
            while time.monotonic() < end:
                _, _, images = self.collection()
                current = next((i for i in images if self.label(i) == label), None)
                if current is not None:
                    break
                time.sleep(.08)
            if current is None:
                raise RuntimeError('התמונה הנבחרת לא נטענה')
        time.sleep(settle)
        self.click(current, right=True)
        item = self.find(lambda e: self.attr(e, 'AXRole') == 'AXMenuItem'
                         and self.label(e).replace('…', '').replace('...', '').strip() == 'Save Image As')
        self.press(item)
        self.select_downloads()
        field = self.find(lambda e: self.attr(e, 'AXIdentifier') == 'saveAsNameTextField')
        default_name = str(self.attr(field, 'AXValue', '')).strip()
        if not default_name:
            raise RuntimeError('לא ניתן לקרוא את שם הקובץ ש-Chrome הציע.')
        before = self.download_snapshot(destination)
        # The filename field is already focused in the native save panel.
        # Enter activates the default Save button immediately and avoids an
        # expensive accessibility-tree scan for OKButton.
        self.key(36)  # Return / Enter
        target = self.wait_for_new_download(destination, before, default_name)
        dimensions = png_size(target)
        # Move directly to the next fullscreen image. This is substantially
        # faster and more reliable than rescanning/clicking the filmstrip.
        self.key(124)  # macOS virtual key code: Right Arrow
        time.sleep(.12)
        return target, dimensions


def main():
    parser = argparse.ArgumentParser(description='Photoroom → Downloads, using the existing Chrome window')
    parser.add_argument('--settle', type=float, default=.35)
    parser.add_argument('--limit', type=int, default=0, help='Optional limit for a test run')
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    destination = Path.home() / 'Downloads'
    browser = Chrome()
    print('Chrome זוהה. מתחיל מהתמונה המוגדלת הנוכחית.', flush=True)
    time.sleep(.25)
    browser.front_guard()
    strip, buttons, images = browser.collection()
    if args.inspect:
        print(json.dumps({'names': [browser.label(b) for b in buttons], 'strip': browser.frame(strip), 'current': [browser.label(i) for i in images]}, ensure_ascii=False, indent=2))
        return
    # Traverse the fullscreen viewer directly. Save the current image, then
    # advance with Right Arrow and wait until the displayed image actually changes.
    saved_now = 0
    visited = set()

    def current_label():
        _, buttons, images = browser.collection()
        button_labels = {browser.label(b) for b in buttons}
        candidates = [browser.label(i) for i in images if browser.label(i) in button_labels]
        if not candidates:
            raise RuntimeError('לא ניתן לזהות את התמונה המוגדלת הנוכחית.')
        # The fullscreen image is normally the largest matching AXImage.
        ranked = []
        for image in images:
            name = browser.label(image)
            if name not in button_labels:
                continue
            frame = browser.frame(image)
            area = frame[2] * frame[3] if frame else 0
            ranked.append((area, name))
        return max(ranked)[1] if ranked else candidates[0]

    while True:
        check_stop()
        label = current_label()
        if label in visited:
            print(f'הגענו לסוף האוסף. נשמרו {saved_now} תמונות בהרצה זו.', flush=True)
            print(f'הקבצים נשמרו ב־{destination}', flush=True)
            return
        visited.add(label)

        print(f'שומר: {label}', flush=True)
        target, dimensions = browser.save(label, destination, max(.15, args.settle))
        saved_now += 1
        print(f'נשמרו בהרצה זו {saved_now} תמונות; {dimensions[0]}×{dimensions[1]}', flush=True)
        if args.limit and saved_now >= args.limit:
            return

        previous = label
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            time.sleep(.08)
            try:
                if current_label() != previous:
                    break
            except RuntimeError:
                pass
        else:
            print(f'הגענו לסוף האוסף. נשמרו {saved_now} תמונות בהרצה זו.', flush=True)
            print(f'הקבצים נשמרו ב־{destination}', flush=True)
            return


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nנעצר באמצעות Control+C. הקבצים שכבר נשמרו נשארים ב-Downloads.', flush=True)
    except Exception as exc:
        print(f'\nהפעולה נעצרה: {exc}', flush=True)
        raise SystemExit(1)
