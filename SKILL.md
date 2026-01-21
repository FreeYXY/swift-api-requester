---
name: swift-api-requester
description: Generate Swift API request classes and a companion commented-out request method based on a user-provided interface definition (OpenAPI/JSON/YAML summary). Use when the user asks for "api-req", wants a Swift request class built from API docs, or needs a request method template that matches HJServiceRequestInfoBase and FalconFoundation patterns.
---

# Swift API Requester

## Overview

Generate a Swift request class and a commented-out request method from an interface definition (OpenAPI/JSON/YAML snippet or a structured summary). The definition is parsed for request method, path, parameters (names + types), server domain, and response notes.

## Quick Start (Fast Path)

Prefer using `$CODEX_HOME/skills/swift-api-requester/scripts/swift_api_requester.py` to update `HostPath.swift`, generate the request class, and update `project.pbxproj` in a single pass.

Example (run from project root; the parent folder name must be `huajiao_ios`):

```bash
python3  ~/.codex/skills/swift-api-requester/scripts/swift_api_requester.py \\
  --method <METHOD> \\
  --path <PATH> \\
  --summary <SUMMARY> \\
  --server <SERVER_DOMAIN> \\
  --params \"<param1>:<type1>,<param2>:<type2>\"
```

If you are not already in the project root, `cd` into it first. The parent folder name must be `huajiao_ios` to ensure correct path resolution for project files.

Note: the script lives in the skill directory, not the project `scripts/` folder. Use the absolute path (or `$CODEX_HOME`) while running it from the project root.

Where these placeholders are taken from the interface definition:
- `<METHOD>`: HTTP method (GET/POST/PUT/etc.)
- `<PATH>`: request path (e.g., `/equipment/equipment/wear`)
- `<SUMMARY>`: interface summary/description
- `<SERVER_DOMAIN>`: server domain from the doc (before normalization)
- `--params`: request parameters as `name:type` pairs, comma-separated

OpenAPI extraction mapping:
- `<METHOD>`: `paths.<path>.<method>`
- `<PATH>`: the `paths` key (e.g., `/equipment/equipment/wear`)
- `<SUMMARY>`: `paths.<path>.<method>.summary` (fallback to `description` if missing)
- `<SERVER_DOMAIN>`: `servers[0].url` host (strip scheme and path)
- `--params`: requestBody schema properties + parameters list (use type mapping rules below)

## Workflow

1) Always ask for the interface definition data at the start of every use of this skill.
   - Prefer OpenAPI/JSON/YAML snippets.
   - If the user only has a URL, ask them to paste the relevant sections instead.
   - If the user already provided the interface definition in the same message, skip asking and proceed.
2) Extract these fields from the provided data:
   - Request method (GET/POST/PUT/etc.)
   - Request path (e.g., /voice/guide/close_pop)
   - Request parameters with types (e.g., userid: string, count: int)
   - Server domain (e.g., passport.inner.test.huajiao.com)
   - Response fields (optional; only if needed by the user)
3) Normalize the server domain:
   - Keep only the first label and the last two labels of the domain.
   - Remove everything between them, regardless of format (e.g., .inner, .inner-test, .test, etc.).
   - Example: payment.inner-test.yuanqijiaoyou.com -> payment.yuanqijiaoyou.com
   - Example: passport.inner.test.huajiao.com -> passport.huajiao.com
4) Read `living/Classes/Swift/Networking/HostPath.swift`:
   - In `extension Host`, use an existing Host if it matches the normalized domain.
   - If missing, add a new Host entry for the normalized domain.
     - Example: domain passport.huajiao.com -> `static let passport = Host(rawValue: "passport.huajiao.com")`
     - The request class must use `Host.passport.rawValue`.
   - In `extension Path`, use an existing Path for the request path.
   - If missing, add a new Path entry for the request path.
     - Use the API summary as the comment for the new Path entry.
     - Example: summary "用户搜索" + path /user/search -> `/// 用户搜索` then `static let userSearch = Path(rawValue: "/user/search")`
     - The request class must use `Path.userSearch.rawValue`.
5) Generate the Swift request class following the rules below.
6) Generate the commented-out request method template following the rules below.
7) Output destination:
   - Always write the generated Swift class file to `huajiao_ios/living/Classes/Swift/Networking/Request` (assume it already exists).
   - If the user provides an explicit output path or filename, use it and override the default.
8) Xcode project integration (always):
   - Prefer running `$CODEX_HOME/skills/swift-api-requester/scripts/swift_api_requester.py` to update `HostPath.swift` and `living.xcodeproj/project.pbxproj` in one pass.
   - Always add the generated folder and file(s) to `living.xcodeproj/project.pbxproj` under the `living` target.
   - If the `Request` folder already exists in the project, only add the newly generated class file.
   - Even when the project is opened via `living.xcworkspace` (CocoaPods), changes to `living.xcodeproj` will appear in the workspace because the workspace references that project.

## Output Rules

### Swift Request Class

1) File header:
   - Include `import FalconFoundation`.
   - Add other imports only if required by existing project conventions; default to FalconFoundation only.
2) Class definition:
   - `@objcMembers class <ClassName>: HJServiceRequestInfoBase { ... }`
3) Class naming:
   - Derive from the request path. Convert the path into PascalCase and append `Request`.
   - Rules: trim leading/trailing slashes; split by `/`, `_`, `-`, and `.`; capitalize each component; join; add `Request`.
   - Example: `/voice/guide/close_pop` -> `VoiceGuideClosePopRequest`.
4) Properties for request parameters:
   - For each request parameter, add `var <name>: <SwiftType>?`
   - Keep the parameter name exactly as in the API doc (camelCase or snake_case).
5) Types mapping:
   - string -> `String`
   - int/integer -> `Int`
   - long -> `Int64`
   - float/double/number -> `Double`
   - bool/boolean -> `Bool`
   - array -> `[Any]` (or `[String]` if specified)
   - object/map/dict -> `[String: Any]`
   - unknown -> `String` and ask the user for clarification
6) Override `host`, `path`, and `params`:
   - `host`: use the matching Host from `HostPath.swift` for the normalized server domain.
   - `path`: use the matching Path from `HostPath.swift` for the request path.
   - `params`: MUST be output verbatim, character-for-character, from the fixed override below. Do not change the type, add/remove `return`, or alter formatting.
     - `override var params: [AnyHashable : Any] { return pep_dictionaryWithValues(forExceptKeys: HJServiceRequestInfoBase.pep_allPropertyKeys as! [String]) }`

### Hard Requirement (Verbatim + Self-Check)

After generating the request class, perform a self-check that the `params` override exactly matches the fixed snippet below. If it does not match, the output is invalid and must be corrected before returning.

Fixed snippet (COPY EXACTLY):

```
override var params: [AnyHashable : Any] {
    return pep_dictionaryWithValues(forExceptKeys: HJServiceRequestInfoBase.pep_allPropertyKeys as! [String])
}
```

### Commented-Out Request Method

Generate a Swift method outside the request class and comment it out line-by-line using `//`.

Rules for the method:
1) Method name is fixed to `request`.
2) Instantiate the request class derived above.
3) For each request parameter, add an assignment line:
   - `request.<paramName> = <#paramName#>`
4) Choose the network call based on the request method:
   - GET: `pep_networkTaskController.getRequestInfo(request) { _, err in`
   - POST: `pep_networkTaskController.postJSONRequestInfo(request) { [weak self] result, err in`
5) Use the fixed error block exactly:
   - `if err != nil { DDLogError(" error \(String(describing: err?.localizedDescription))") }`
6) The entire method must be commented out and placed outside the class.

Template:

```swift
// func request() {
//     let request = <ClassName>()
//     request.param1 = <#param1#>
//     request.param2 = <#param2#>
//     pep_networkTaskController.getRequestInfo(request) { _, err in
//         if err != nil {
//             DDLogError(" error \(String(describing: err?.localizedDescription))")
//         }
//     }
// }
```

POST template:

```swift
// func request() {
//     let request = <ClassName>()
//     request.param1 = <#param1#>
//     request.param2 = <#param2#>
//     pep_networkTaskController.postJSONRequestInfo(request) { [weak self] result, err in
//         if err != nil {
//             DDLogError(" error \(String(describing: err?.localizedDescription))")
//         }
//     }
// }
```

## Response Format

Return only:
1) The Swift request class.
2) The commented-out request method.

If anything is missing or ambiguous in the API doc, ask the user before generating code.

If writing a file:
- Use `<ClassName>.swift` as the filename unless the user specifies otherwise.

## references/

Use this folder to store any saved API docs or examples if the user wants to keep them for reuse.
