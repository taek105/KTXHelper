import AppKit
import ApplicationServices
import Carbon
import Foundation

// A small macOS accessibility bridge. It never connects to Chrome's debugging interface; 
//actions are macOS mouse/keyboard events or accessibility presses.
let arguments = CommandLine.arguments
guard arguments.count >= 3, let processID = Int32(arguments[2]) else {
    fputs("usage: native_browser <snapshot|navigate|refresh|press|click|type|close> <pid> [argument]\n", stderr)
    exit(2)
}
guard AXIsProcessTrusted() else {
    fputs("macOS Accessibility permission is required for this application.\n", stderr)
    exit(3)
}
guard let runningApp = NSRunningApplication(processIdentifier: processID) else {
    fputs("Chrome process has closed.\n", stderr)
    exit(4)
}
let app = AXUIElementCreateApplication(pid_t(processID))

func attribute(_ element: AXUIElement, _ name: String) -> String {
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &raw) == .success,
          let raw, CFGetTypeID(raw) != AXUIElementGetTypeID() else { return "" }
    return "\(raw)"
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &raw) == .success else {
        return []
    }
    return raw as? [AXUIElement] ?? []
}

func firstWindow() -> AXUIElement? {
    var focused: CFTypeRef?
    if AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &focused) == .success,
       let focused, CFGetTypeID(focused) == AXUIElementGetTypeID() {
        return focused as! AXUIElement
    }
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &raw) == .success else {
        return nil
    }
    return (raw as? [AXUIElement])?.first
}

func activate(fast: Bool = false) throws {
    guard let window = firstWindow() else { throw NSError(domain: "Chrome window closed", code: 4) }
    _ = runningApp.activate(options: [])
    _ = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
    if fast {
        let deadline = Date().addingTimeInterval(1.5)
        var lastAttempt = Date()
        while NSWorkspace.shared.frontmostApplication?.processIdentifier != processID {
            guard Date() < deadline else {
                throw NSError(domain: "Chrome window did not become active", code: 11)
            }
            if Date().timeIntervalSince(lastAttempt) >= 0.2 {
                _ = runningApp.activate(options: [])
                _ = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
                lastAttempt = Date()
            }
            Thread.sleep(forTimeInterval: 0.02)
        }
    } else {
        Thread.sleep(forTimeInterval: 0.2)
    }
}

func key(_ code: CGKeyCode, command: Bool = false, shift: Bool = false,
         pause: TimeInterval = 0.1) {
    var flags: CGEventFlags = []
    if command { flags.insert(.maskCommand) }
    if shift { flags.insert(.maskShift) }
    for down in [true, false] {
        if let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) {
            event.flags = flags
            event.post(tap: .cghidEventTap)
        }
    }
    Thread.sleep(forTimeInterval: pause)
}

func typeText(_ text: String, careful: Bool = false) throws {
    // Virtual key codes are US keyboard positions. Select an ASCII layout so
    // the user's Korean input mode cannot change the entered credentials.
    let originalSource = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
    let asciiSource = TISCopyCurrentASCIICapableKeyboardInputSource().takeRetainedValue()
    let changedSource = !CFEqual(originalSource, asciiSource)
    if changedSource {
        let result = TISSelectInputSource(asciiSource)
        guard result == noErr else {
            throw NSError(domain: "Could not select ASCII keyboard input", code: Int(result))
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    defer {
        if changedSource { _ = TISSelectInputSource(originalSource) }
    }
    let base = Array("abcdefghijklmnopqrstuvwxyz1234567890-=[]\\;',./`")
    let shifted = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%^&*()_+{}|:\"<>?~")
    let codes: [CGKeyCode] = [
        0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46,
        45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6,
        18, 19, 20, 21, 23, 22, 26, 28, 25, 29,
        27, 24, 33, 30, 42, 41, 39, 43, 47, 44, 50,
    ]
    for character in text {
        if let index = base.firstIndex(of: character) {
            key(codes[index], pause: careful ? 0.1 : 0.035)
            if careful { Thread.sleep(forTimeInterval: Double.random(in: 0.05...0.16)) }
            continue
        }
        if let index = shifted.firstIndex(of: character) {
            key(codes[index], shift: true, pause: careful ? 0.1 : 0.035)
            if careful { Thread.sleep(forTimeInterval: Double.random(in: 0.05...0.16)) }
            continue
        }
        let units = Array(String(character).utf16)
        units.withUnsafeBufferPointer { buffer in
            guard let pointer = buffer.baseAddress else { return }
            for down in [true, false] {
                if let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down) {
                    event.keyboardSetUnicodeString(stringLength: units.count, unicodeString: pointer)
                    event.post(tap: .cghidEventTap)
                }
            }
        }
        Thread.sleep(forTimeInterval: careful ? 0.1 : 0.035)
    }
}

func pasteText(_ text: String) throws {
    let board = NSPasteboard.general
    let saved = (board.pasteboardItems ?? []).map { item -> NSPasteboardItem in
        let copy = NSPasteboardItem()
        for type in item.types {
            if let data = item.data(forType: type) { copy.setData(data, forType: type) }
        }
        return copy
    }
    defer {
        board.clearContents()
        if !saved.isEmpty { _ = board.writeObjects(saved) }
    }
    board.clearContents()
    guard board.setString(text, forType: .string) else {
        throw NSError(domain: "Could not write password to clipboard", code: 13)
    }
    key(9, command: true) // Command-V
    Thread.sleep(forTimeInterval: 0.1)
}

func clickElement(_ target: AXUIElement, fast: Bool = false) throws {
    _ = AXUIElementPerformAction(target, "AXScrollToVisible" as CFString)
    Thread.sleep(forTimeInterval: fast ? 0.04 : 0.25)
    var positionRef: CFTypeRef?
    var sizeRef: CFTypeRef?
    guard AXUIElementCopyAttributeValue(target, kAXPositionAttribute as CFString, &positionRef) == .success,
          AXUIElementCopyAttributeValue(target, kAXSizeAttribute as CFString, &sizeRef) == .success,
          let positionValue = positionRef,
          let sizeValue = sizeRef,
          CFGetTypeID(positionValue) == AXValueGetTypeID(),
          CFGetTypeID(sizeValue) == AXValueGetTypeID() else {
        throw NSError(domain: "Could not locate browser control", code: 8)
    }
    var position = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &position),
          AXValueGetValue(sizeValue as! AXValue, .cgSize, &size) else {
        throw NSError(domain: "Could not read browser control position", code: 9)
    }
    let center = CGPoint(x: position.x + size.width / 2, y: position.y + size.height / 2)
    for type in [CGEventType.mouseMoved, .leftMouseDown, .leftMouseUp] {
        guard let event = CGEvent(mouseEventSource: nil, mouseType: type,
                                  mouseCursorPosition: center, mouseButton: .left) else { continue }
        event.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: fast ? 0.015 : (type == .mouseMoved ? 0.2 : 0.06))
    }
}

func isFocused(_ target: AXUIElement) -> Bool {
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(target, kAXFocusedAttribute as CFString, &raw) == .success,
          let raw, CFGetTypeID(raw) == CFBooleanGetTypeID() else { return false }
    return CFBooleanGetValue(raw as! CFBoolean)
}

func snapshot() throws {
    guard let window = firstWindow() else { throw NSError(domain: "Chrome window closed", code: 4) }
    var nodes: [[String: Any]] = []
    func walk(_ element: AXUIElement, _ path: String, _ depth: Int) {
        guard depth < 40, nodes.count < 4000 else { return }
        let role = attribute(element, kAXRoleAttribute)
        let title = attribute(element, kAXTitleAttribute)
        let description = attribute(element, kAXDescriptionAttribute)
        let enabled = attribute(element, kAXEnabledAttribute)
        let value = role == "AXTextField" && title != "주소창 및 검색창" || role == "AXSecureTextField"
            ? "" : attribute(element, kAXValueAttribute)
        nodes.append(["path": path, "role": role, "title": title,
                      "description": description, "value": value,
                      "enabled": enabled])
        for (index, child) in children(element).enumerated() {
            walk(child, path + "." + String(index), depth + 1)
        }
    }
    walk(window, "root", 0)
    let data = try JSONSerialization.data(withJSONObject: nodes)
    FileHandle.standardOutput.write(data)
}

func navigate(_ destination: String) throws {
    try activate()
    let board = NSPasteboard.general
    let saved = (board.pasteboardItems ?? []).map { item -> NSPasteboardItem in
        let copy = NSPasteboardItem()
        for type in item.types {
            if let data = item.data(forType: type) { copy.setData(data, forType: type) }
        }
        return copy
    }
    key(37, command: true) // Command-L
    board.clearContents()
    guard board.setString(destination, forType: .string) else {
        throw NSError(domain: "Could not write URL to clipboard", code: 5)
    }
    key(9, command: true) // Command-V
    key(36) // Return
    Thread.sleep(forTimeInterval: 0.25)
    board.clearContents()
    if !saved.isEmpty { _ = board.writeObjects(saved) }
}

func element(at path: String) -> AXUIElement? {
    guard let window = firstWindow() else { return nil }
    let parts = path.split(separator: ".")
    guard parts.first == "root" else { return nil }
    var current = window
    for part in parts.dropFirst() {
        let siblings = children(current)
        guard let index = Int(part), siblings.indices.contains(index) else { return nil }
        current = siblings[index]
    }
    return current
}

func refreshWithoutFocus() throws {
    guard let window = firstWindow() else { throw NSError(domain: "Chrome window closed", code: 4) }
    func refreshButton(in element: AXUIElement, inToolbar: Bool, depth: Int) -> AXUIElement? {
        guard depth < 12 else { return nil }
        let role = attribute(element, kAXRoleAttribute)
        let toolbar = inToolbar || role == "AXToolbar"
        let title = attribute(element, kAXTitleAttribute)
        if toolbar && role == "AXButton" && (title == "새로고침" || title == "다시 로드") {
            return element
        }
        for child in children(element) {
            if let found = refreshButton(in: child, inToolbar: toolbar, depth: depth + 1) {
                return found
            }
        }
        return nil
    }
    guard let button = refreshButton(in: window, inToolbar: false, depth: 0) else {
        throw NSError(domain: "Chrome refresh button not found", code: 10)
    }
    let result = AXUIElementPerformAction(button, kAXPressAction as CFString)
    guard result == .success else {
        throw NSError(domain: "Chrome background refresh failed", code: Int(result.rawValue))
    }
}

do {
    switch arguments[1] {
    case "snapshot": try snapshot()
    case "navigate":
        guard arguments.count > 3 else { exit(2) }
        try navigate(arguments[3])
    case "refresh":
        try refreshWithoutFocus()
    case "press":
        guard arguments.count > 5, let target = element(at: arguments[3]) else { exit(5) }
        let role = attribute(target, kAXRoleAttribute)
        let title = attribute(target, kAXTitleAttribute)
        guard role == arguments[4], title == arguments[5] else { exit(6) }
        try activate()
        var result = AXUIElementPerformAction(target, kAXPressAction as CFString)
        if result != .success {
            _ = AXUIElementPerformAction(target, "AXScrollToVisible" as CFString)
            Thread.sleep(forTimeInterval: 0.2)
            result = AXUIElementPerformAction(target, kAXPressAction as CFString)
        }
        guard result == .success else { throw NSError(domain: "Accessibility press failed", code: Int(result.rawValue)) }
    case "click", "click_fast":
        guard arguments.count > 5, let target = element(at: arguments[3]) else { exit(5) }
        let role = attribute(target, kAXRoleAttribute)
        let title = attribute(target, kAXTitleAttribute)
        guard role == arguments[4], title == arguments[5] else { exit(6) }
        let fast = arguments[1] == "click_fast"
        try activate(fast: fast)
        try clickElement(target, fast: fast)
    case "type", "type_careful", "type_paste":
        guard arguments.count > 5, let target = element(at: arguments[3]) else { exit(5) }
        let role = attribute(target, kAXRoleAttribute)
        let title = attribute(target, kAXTitleAttribute)
        guard role == arguments[4], title == arguments[5],
              role == "AXTextField" || role == "AXSecureTextField" else { exit(6) }
        try activate()
        try clickElement(target)
        if !isFocused(target) {
            _ = AXUIElementSetAttributeValue(target, kAXFocusedAttribute as CFString, kCFBooleanTrue)
            Thread.sleep(forTimeInterval: 0.1)
        }
        guard isFocused(target) else {
            throw NSError(domain: "Browser input field did not receive focus", code: 12)
        }
        key(0, command: true) // Command-A in the focused field
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let text = String(data: input, encoding: .utf8) else { exit(7) }
        if arguments[1] == "type_paste" {
            try pasteText(text)
            var valueRef: CFTypeRef?
            guard AXUIElementCopyAttributeValue(target, kAXValueAttribute as CFString, &valueRef) == .success,
                  let value = valueRef as? String, value.count == text.count else {
                throw NSError(domain: "Password paste did not fill the input", code: 14)
            }
        } else {
            try typeText(text, careful: arguments[1] == "type_careful")
        }
    case "close": runningApp.terminate()
    default: exit(2)
    }
} catch {
    fputs("\(error)\n", stderr)
    exit(1)
}
