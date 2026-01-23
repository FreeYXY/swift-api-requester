#!/usr/bin/env python3
import argparse
import json
import re
import sys
import uuid
from pathlib import Path

PARAM_TYPE_MAP = {
    "string": "String",
    "int": "Int",
    "integer": "Int",
    "long": "Int64",
    "float": "Double",
    "double": "Double",
    "number": "Double",
    "bool": "Bool",
    "boolean": "Bool",
    "array": "[Any]",
    "object": "[String: Any]",
    "map": "[String: Any]",
    "dict": "[String: Any]",
}

RESPONSE_TYPE_MAP = {
    "string": "String",
    "int": "Int",
    "integer": "Int",
    "long": "Int64",
    "float": "Double",
    "double": "Double",
    "number": "Double",
    "bool": "Bool",
    "boolean": "Bool",
}

SWIFT_KEYWORDS = {
    "associatedtype",
    "class",
    "deinit",
    "enum",
    "extension",
    "fileprivate",
    "func",
    "import",
    "init",
    "inout",
    "internal",
    "let",
    "open",
    "operator",
    "private",
    "protocol",
    "public",
    "static",
    "struct",
    "subscript",
    "typealias",
    "var",
    "break",
    "case",
    "continue",
    "default",
    "defer",
    "do",
    "else",
    "fallthrough",
    "for",
    "guard",
    "if",
    "in",
    "repeat",
    "return",
    "switch",
    "where",
    "while",
    "as",
    "Any",
    "catch",
    "false",
    "is",
    "nil",
    "rethrows",
    "super",
    "self",
    "Self",
    "throw",
    "throws",
    "true",
    "try",
    "associativity",
    "convenience",
    "dynamic",
    "didSet",
    "final",
    "get",
    "infix",
    "indirect",
    "lazy",
    "left",
    "mutating",
    "none",
    "nonmutating",
    "optional",
    "override",
    "postfix",
    "precedence",
    "prefix",
    "Protocol",
    "required",
    "right",
    "set",
    "Type",
    "unowned",
    "weak",
    "willSet",
}

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)

def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)

def note_no_data(raw_response: str) -> None:
    print("没有需要解析的数据", file=sys.stderr)
    if raw_response:
        print("原始接口响应:", file=sys.stderr)
        print(raw_response, file=sys.stderr)


def normalize_domain(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) < 3:
        return domain
    return f"{parts[0]}.{parts[-2]}.{parts[-1]}"


def sanitize_identifier(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if re.match(r"^[0-9]", value):
        value = f"_{value}"
    return value


def to_pascal(path: str) -> str:
    path = path.strip("/")
    if not path:
        return "Request"
    parts = re.split(r"[/_.-]+", path)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)

def to_pascal_type(value: str) -> str:
    name = to_pascal(value)
    name = re.sub(r"[^A-Za-z0-9]", "", name)
    if not name:
        name = "Type"
    if re.match(r"^[0-9]", name):
        name = f"Type{name}"
    return name


def to_lower_camel(value: str) -> str:
    if not value:
        return value
    return value[:1].lower() + value[1:]


def parse_host_entries(content: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for match in re.finditer(r"static let (\w+) = Host\(rawValue: \"([^\"]+)\"\)", content):
        entries[match.group(1)] = match.group(2)
    return entries


def build_unique_host_name(domain: str, existing: dict[str, str]) -> str:
    parts = domain.split(".")
    base_name = sanitize_identifier(parts[0]) if parts else "host"
    if base_name not in existing:
        return base_name
    if existing.get(base_name) == domain:
        return base_name

    suffix_base = sanitize_identifier(parts[1]) if len(parts) > 1 else "host"
    candidate = f"{base_name}_{suffix_base}" if suffix_base else base_name
    if candidate not in existing or existing.get(candidate) == domain:
        return candidate

    idx = 2
    while True:
        candidate_i = f"{candidate}_{idx}"
        if candidate_i not in existing or existing.get(candidate_i) == domain:
            return candidate_i
        idx += 1


def ensure_host_entry(content: str, domain: str) -> tuple[str, str, bool]:
    match = re.search(rf"static let (\w+) = Host\(rawValue: \"{re.escape(domain)}\"\)", content)
    if match:
        return content, match.group(1), False

    existing = parse_host_entries(content)
    host_name = build_unique_host_name(domain, existing)
    block_match = re.search(r"extension Host \{([\s\S]*?)\n\}\n\nextension Path \{", content)
    if not block_match:
        die("failed to locate Host extension in HostPath.swift")

    insert_at = block_match.end(1)
    insertion = f"\n    static let {host_name} = Host(rawValue: \"{domain}\")\n"
    new_content = content[:insert_at] + insertion + content[insert_at:]
    return new_content, host_name, True


def ensure_path_entry(content: str, path_value: str, summary: str, default_name: str) -> tuple[str, str, bool]:
    match = re.search(rf"static let (\w+) = Path\(rawValue: \"{re.escape(path_value)}\"\)", content)
    if match:
        return content, match.group(1), False

    block_match = re.search(r"extension Path \{([\s\S]*?)\n\}\n\nstruct HostPath", content)
    if not block_match:
        die("failed to locate Path extension in HostPath.swift")

    insert_at = block_match.end(1)
    insertion = f"\n    /// {summary}\n    static let {default_name} = Path(rawValue: \"{path_value}\")\n"
    new_content = content[:insert_at] + insertion + content[insert_at:]
    return new_content, default_name, True


def update_host_path(
    host_path_file: Path, path_value: str, summary: str, domain: str, write_changes: bool = True
) -> tuple[str, str, bool]:
    content = host_path_file.read_text(encoding="utf-8")

    normalized = normalize_domain(domain)
    content, host_name, host_changed = ensure_host_entry(content, normalized)

    base_name = to_pascal(path_value)
    path_name_default = to_lower_camel(base_name)
    content, path_name, path_changed = ensure_path_entry(content, path_value, summary, path_name_default)

    changed = host_changed or path_changed
    if write_changes and changed:
        host_path_file.write_text(content, encoding="utf-8")
    return host_name, path_name, changed


def map_param_type(raw_type: str) -> str:
    key = raw_type.strip().lower()
    return PARAM_TYPE_MAP.get(key, "String")

def map_response_scalar_type(raw_type: str) -> str:
    key = raw_type.strip().lower()
    swift_type = RESPONSE_TYPE_MAP.get(key)
    if swift_type:
        return swift_type
    warn(f"unknown response type '{raw_type}', defaulting to String")
    return "String"


def map_response_type(raw_type: str) -> str:
    raw = raw_type.strip().lower()
    if raw.endswith("[]"):
        inner = raw[:-2]
        return f"[{map_response_scalar_type(inner)}]"
    if raw.startswith("array<") and raw.endswith(">"):
        inner = raw[6:-1]
        return f"[{map_response_scalar_type(inner)}]"
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        return f"[{map_response_scalar_type(inner)}]"
    if raw in {"array", "list"}:
        die("response array type requires element type, e.g. items:[string] or items:array<int>")
    if raw in {"object", "map", "dict"}:
        die("response object type requires JSON schema or example payload")
    return map_response_scalar_type(raw)


def format_property_name(name: str) -> str:
    if IDENTIFIER_PATTERN.match(name):
        if name in SWIFT_KEYWORDS:
            return f"`{name}`"
        return name
    die(f"response field name '{name}' is not a valid Swift identifier; strict spelling is required")


def build_request_class(class_name: str, host_name: str, path_name: str, params: list[tuple[str, str]]) -> str:
    lines = [
        "import FalconFoundation",
        "",
        f"@objcMembers class {class_name}: HJServiceRequestInfoBase {{",
    ]

    for name, swift_type in params:
        lines.append(f"    var {name}: {swift_type}?")

    lines += [
        "",
        f"    override var host: String {{ Host.{host_name}.rawValue }}",
        "",
        f"    override var path: String {{ Path.{path_name}.rawValue }}",
        "",
        "    override var params: [AnyHashable : Any] {",
        "        return pep_dictionaryWithValues(forExceptKeys: HJServiceRequestInfoBase.pep_allPropertyKeys as! [String])",
        "    }",
        "}",
    ]

    return "\n".join(lines)

def build_request_method(class_name: str, params: list[tuple[str, str]], method: str) -> str:
    method = method.upper()
    if method == "GET":
        call_line = "    pep_networkTaskController.getRequestInfo(request) { _, err in"
    elif method == "POST":
        call_line = "    pep_networkTaskController.postJSONRequestInfo(request) { [weak self] result, err in"
    else:
        die(f"unsupported method: {method}")

    lines = [
        "// func request() {",
        f"//     let request = {class_name}()",
    ]
    for name, _ in params:
        lines.append(f"//     request.{name} = <#{name}#>")
    lines.append(f"//{call_line}")
    lines.append("//         if err != nil {")
    lines.append("//             DDLogError(\" error \\(String(describing: err?.localizedDescription))\")")
    lines.append("//         }")
    lines.append("//     }")
    lines.append("// }")

    return "\n".join(lines)


def parse_params(param_str: str) -> list[tuple[str, str]]:
    if not param_str:
        return []
    params = []
    for item in param_str.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            die(f"param missing type: {item}")
        name, raw_type = item.split(":", 1)
        params.append((name.strip(), map_param_type(raw_type)))
    return params


def insert_after_line(content: str, needle: str, new_line: str) -> str:
    if new_line.strip() in content:
        return content
    idx = content.find(needle)
    if idx == -1:
        die(f"failed to locate insertion point: {needle}")
    line_end = content.find("\n", idx)
    if line_end == -1:
        die("unexpected file format")
    return content[: line_end + 1] + new_line + content[line_end + 1 :]

def parse_response_fields(response_str: str) -> list[tuple[str, str]]:
    if not response_str:
        return []
    fields = []
    for item in response_str.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            die(f"response field missing type: {item}")
        name, raw_type = item.split(":", 1)
        fields.append((name.strip(), map_response_type(raw_type)))
    return fields


def unique_struct_name(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    idx = 2
    while True:
        candidate = f"{base}{idx}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        idx += 1


def build_struct_definition(struct_name: str, fields: list[tuple[str, str, str]]) -> str:
    lines = [f"struct {struct_name}: Codable {{"]
    for prop_name, swift_type, _ in fields:
        lines.append(f"    let {prop_name}: {swift_type}?")

    needs_keys = any(prop_name != original for prop_name, _, original in fields)
    if needs_keys:
        lines.append("")
        lines.append("    private enum CodingKeys: String, CodingKey {")
        for prop_name, _, original in fields:
            if prop_name == original:
                lines.append(f"        case {prop_name}")
            else:
                lines.append(f"        case {prop_name} = \"{original}\"")
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines)


def build_single_value_struct(struct_name: str, prop_name: str, swift_type: str) -> str:
    lines = [
        f"struct {struct_name}: Codable {{",
        f"    let {prop_name}: {swift_type}",
        "",
        f"    init({prop_name}: {swift_type}) {{",
        f"        self.{prop_name} = {prop_name}",
        "    }",
        "",
        "    init(from decoder: Decoder) throws {",
        "        let container = try decoder.singleValueContainer()",
        f"        {prop_name} = try container.decode({swift_type}.self)",
        "    }",
        "",
        "    func encode(to encoder: Encoder) throws {",
        "        var container = encoder.singleValueContainer()",
        f"        try container.encode({prop_name})",
        "    }",
        "}",
    ]
    return "\n".join(lines)


def infer_json_type(value, parent_name: str, key: str, structs: list[str], used: set[str]) -> str:
    if isinstance(value, dict):
        child_base = f"{parent_name}{to_pascal_type(key)}"
        child_name = unique_struct_name(child_base, used)
        build_struct_from_json(child_name, value, structs, used)
        return child_name
    if isinstance(value, list):
        element_type = infer_list_element_type(value, parent_name, key, structs, used)
        return f"[{element_type}]"
    if isinstance(value, bool):
        return "Bool"
    if isinstance(value, int):
        return "Int"
    if isinstance(value, float):
        return "Double"
    if isinstance(value, str):
        return "String"
    warn(f"null or unknown response value for key '{key}', defaulting to String")
    return "String"


def infer_list_element_type(values: list, parent_name: str, key: str, structs: list[str], used: set[str]) -> str:
    non_null = [item for item in values if item is not None]
    if not non_null:
        warn(f"empty array for key '{key}', defaulting to [String]")
        return "String"
    first = non_null[0]
    if isinstance(first, dict):
        child_base = f"{parent_name}{to_pascal_type(key)}Item"
        child_name = unique_struct_name(child_base, used)
        build_struct_from_json(child_name, first, structs, used)
        return child_name
    if isinstance(first, list):
        nested = infer_list_element_type(first, parent_name, f"{key}Item", structs, used)
        return f"[{nested}]"
    if isinstance(first, bool):
        return "Bool"
    if isinstance(first, int):
        return "Int"
    if isinstance(first, float):
        return "Double"
    if isinstance(first, str):
        return "String"
    warn(f"unknown array element type for key '{key}', defaulting to String")
    return "String"


def build_struct_from_json(struct_name: str, payload: dict, structs: list[str], used: set[str]) -> None:
    fields: list[tuple[str, str, str]] = []
    for key, value in payload.items():
        prop_name = format_property_name(key)
        swift_type = infer_json_type(value, struct_name, key, structs, used)
        fields.append((prop_name, swift_type, key))
    structs.append(build_struct_definition(struct_name, fields))


def build_response_models(base_model_name: str, response_payload, response_fields: list[tuple[str, str]]) -> str:
    if response_payload is None and not response_fields:
        return ""

    if response_payload is None:
        fields: list[tuple[str, str, str]] = []
        for name, swift_type in response_fields:
            prop_name = format_property_name(name)
            fields.append((prop_name, swift_type, name))
        return build_struct_definition(base_model_name, fields)

    used: set[str] = set()
    structs: list[str] = []

    if isinstance(response_payload, dict):
        root_name = unique_struct_name(base_model_name, used)
        build_struct_from_json(root_name, response_payload, structs, used)
        return "\n\n".join(structs)

    if isinstance(response_payload, list):
        element_type = infer_list_element_type(response_payload, base_model_name, "items", structs, used)
        root_name = unique_struct_name(base_model_name, used)
        structs.append(build_single_value_struct(root_name, "items", f"[{element_type}]"))
        return "\n\n".join(structs)

    if isinstance(response_payload, bool):
        swift_type = "Bool"
    elif isinstance(response_payload, int):
        swift_type = "Int"
    elif isinstance(response_payload, float):
        swift_type = "Double"
    elif isinstance(response_payload, str):
        swift_type = "String"
    else:
        warn("unknown top-level response value, defaulting to String")
        swift_type = "String"
    root_name = unique_struct_name(base_model_name, used)
    structs.append(build_single_value_struct(root_name, "value", swift_type))
    return "\n\n".join(structs)


def extract_data_payload(response_payload):
    if isinstance(response_payload, dict):
        if "data" not in response_payload:
            return None, False
        data_value = response_payload.get("data")
        if data_value is None:
            return None, False
        return data_value, True
    return None, False


def find_group_block(content: str, group_id: str, name: str) -> tuple[str, int, int]:
    pattern = re.compile(rf"{group_id} /\* {re.escape(name)} \*/ = \{{\n(?P<body>[\s\S]*?)\n\s*\}};", re.MULTILINE)
    match = pattern.search(content)
    if not match:
        die(f"failed to locate {name} group in project.pbxproj")
    return match.group("body"), match.start("body"), match.end("body")


def find_networking_child_group_id(content: str, child_name: str) -> str:
    group_pattern = re.compile(r"(?P<id>[A-F0-9]{24}) /\* Networking \*/ = \{\n(?P<body>[\s\S]*?)\n\s*\};", re.MULTILINE)
    for match in group_pattern.finditer(content):
        body = match.group("body")
        if "HostPath.swift" not in body:
            continue
        children_match = re.search(r"children = \(\n(?P<children>[\s\S]*?)\n\s*\);", body)
        if not children_match:
            continue
        children = children_match.group("children")
        child_match = re.search(rf"([A-F0-9]{{24}}) /\* {re.escape(child_name)} \*/", children)
        if child_match:
            return child_match.group(1)
    return ""


def update_group_children(content: str, group_id: str, group_name: str, file_ref_id: str, class_filename: str) -> str:
    body, start, end = find_group_block(content, group_id, group_name)
    children_match = re.search(r"children = \(\n(?P<children>[\s\S]*?)\n\s*\);", body)
    if not children_match:
        die(f"failed to locate {group_name} group children")
    children = children_match.group("children")
    if class_filename in children:
        return content

    indent = "\t\t\t\t"
    new_child_line = f"{indent}{file_ref_id} /* {class_filename} */,"
    new_children = children + "\n" + new_child_line

    new_body = body[: children_match.start("children")] + new_children + body[children_match.end("children") :]
    return content[:start] + new_body + content[end:]


def update_pbxproj(pbxproj_file: Path, class_filename: str, group_name: str, require_group: bool = True) -> None:
    content = pbxproj_file.read_text(encoding="utf-8")

    if class_filename in content:
        return

    file_ref_id = uuid.uuid4().hex[:24].upper()
    build_file_id = uuid.uuid4().hex[:24].upper()

    hostpath_build_line = "\t\t2ABA3CE027EDB030006B1E7E /* HostPath.swift in Sources */ = {isa = PBXBuildFile; fileRef = 2ABA3CDF27EDB030006B1E7E /* HostPath.swift */; };"
    build_line = f"\t\t{build_file_id} /* {class_filename} in Sources */ = {{isa = PBXBuildFile; fileRef = {file_ref_id} /* {class_filename} */; }};\n"
    content = insert_after_line(content, hostpath_build_line, build_line)

    hostpath_file_ref_line = "\t\t2ABA3CDF27EDB030006B1E7E /* HostPath.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = HostPath.swift; sourceTree = \"<group>\"; };"
    file_ref_line = f"\t\t{file_ref_id} /* {class_filename} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = {class_filename}; sourceTree = \"<group>\"; }};\n"
    content = insert_after_line(content, hostpath_file_ref_line, file_ref_line)

    target_group_id = find_networking_child_group_id(content, group_name)
    if target_group_id:
        content = update_group_children(content, target_group_id, group_name, file_ref_id, class_filename)
    elif require_group:
        die(f"{group_name} group under Swift Networking not found in project.pbxproj")
    else:
        warn(f"{group_name} group under Swift Networking not found in project.pbxproj; skipping group insertion")

    sources_insert_needle = "\t\t\t\t2ABA3CE027EDB030006B1E7E /* HostPath.swift in Sources */,"
    sources_line = f"\t\t\t\t{build_file_id} /* {class_filename} in Sources */,\n"
    content = insert_after_line(content, sources_insert_needle, sources_line)

    pbxproj_file.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Swift request + response model + update HostPath.swift and project.pbxproj."
    )
    parser.add_argument("--method", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--params", default="")
    parser.add_argument("--response", default="", help="JSON payload or fields list: name:type,name:type")
    parser.add_argument("--response-file", default="", help="Path to JSON response payload file")
    parser.add_argument("--project-root", default=".", help="Project root path containing living/ and living.xcodeproj")
    parser.add_argument(
        "--output-mode",
        choices=["print", "files", "full"],
        default="files",
        help="print: stdout + update HostPath; files: write request + HostPath; full: request + HostPath + pbxproj",
    )

    args = parser.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    host_path_file = root / "living/Classes/Swift/Networking/HostPath.swift"
    pbxproj_file = root / "living.xcodeproj/project.pbxproj"
    request_dir = root / "living/Classes/Swift/Networking/Request"
    model_dir = root / "living/Classes/Swift/Networking/Model"

    if not host_path_file.exists():
        die(f"HostPath.swift not found at {host_path_file}")
    if args.output_mode == "full" and not pbxproj_file.exists():
        die(f"project.pbxproj not found at {pbxproj_file}")

    write_hostpath = args.output_mode in ("print", "files", "full")
    host_name, path_name, _ = update_host_path(
        host_path_file, args.path, args.summary, args.server, write_changes=write_hostpath
    )
    base_class = to_pascal(args.path)
    class_name = f"{base_class}Request"

    params = parse_params(args.params)
    request_class_source = build_request_class(class_name, host_name, path_name, params)
    request_method_source = build_request_method(class_name, params, args.method)

    response_payload = None
    response_fields: list[tuple[str, str]] = []
    raw_response_text = ""
    if args.response_file:
        try:
            raw_response_text = Path(args.response_file).read_text(encoding="utf-8")
            response_payload = json.loads(raw_response_text)
        except FileNotFoundError as exc:
            die(f"response file not found: {exc.filename}")
        except json.JSONDecodeError as exc:
            die(f"invalid response JSON file: {exc}")
    elif args.response:
        trimmed = args.response.strip()
        if trimmed.startswith("{") or trimmed.startswith("["):
            try:
                raw_response_text = args.response
                response_payload = json.loads(trimmed)
            except json.JSONDecodeError as exc:
                die(f"invalid response JSON: {exc}")
        else:
            raw_response_text = args.response
            response_fields = parse_response_fields(args.response)

    base_model_name = f"{base_class}Model"
    response_models_source = ""
    if response_payload is not None:
        data_payload, has_data = extract_data_payload(response_payload)
        if not has_data:
            note_no_data(raw_response_text)
        else:
            response_models_source = build_response_models(base_model_name, data_payload, [])
    elif response_fields:
        response_models_source = build_response_models(base_model_name, None, response_fields)

    print_sections = [request_class_source]
    if response_models_source:
        model_source = response_models_source
        if args.output_mode == "print":
            model_source = "import Foundation\n\n" + response_models_source
        print_sections.append(model_source)
    print_sections.append(request_method_source)
    print_source = "\n\n".join(print_sections) + "\n"

    if args.output_mode == "print":
        print(print_source)
        return

    request_dir.mkdir(parents=True, exist_ok=True)
    request_file = request_dir / f"{class_name}.swift"
    request_file.write_text(request_class_source + "\n\n" + request_method_source + "\n", encoding="utf-8")

    model_file = None
    if response_models_source:
        model_dir.mkdir(parents=True, exist_ok=True)
        model_file = model_dir / f"{base_model_name}.swift"
        model_file.write_text("import Foundation\n\n" + response_models_source + "\n", encoding="utf-8")

    if args.output_mode == "full":
        update_pbxproj(pbxproj_file, request_file.name, "Request", require_group=True)
        if model_file is not None:
            update_pbxproj(pbxproj_file, model_file.name, "Model", require_group=True)


if __name__ == "__main__":
    main()
