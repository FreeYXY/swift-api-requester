#!/usr/bin/env python3
import argparse
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


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


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


def build_request_class(class_name: str, host_name: str, path_name: str, params: list[tuple[str, str]], method: str) -> str:
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
        "",
    ]

    method = method.upper()
    if method == "GET":
        call_line = "    pep_networkTaskController.getRequestInfo(request) { _, err in"
    elif method == "POST":
        call_line = "    pep_networkTaskController.postJSONRequestInfo(request) { [weak self] result, err in"
    else:
        die(f"unsupported method: {method}")

    lines.append("// func request() {")
    lines.append(f"//     let request = {class_name}()")
    for name, _ in params:
        lines.append(f"//     request.{name} = <#{name}#>")
    lines.append(f"//{call_line}")
    lines.append("//         if err != nil {")
    lines.append("//             DDLogError(\" error \\(String(describing: err?.localizedDescription))\")")
    lines.append("//         }")
    lines.append("//     }")
    lines.append("// }")
    lines.append("")

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


def find_group_block(content: str, group_id: str, name: str) -> tuple[str, int, int]:
    pattern = re.compile(rf"{group_id} /\* {re.escape(name)} \*/ = \{{\n(?P<body>[\s\S]*?)\n\s*\}};", re.MULTILINE)
    match = pattern.search(content)
    if not match:
        die(f"failed to locate {name} group in project.pbxproj")
    return match.group("body"), match.start("body"), match.end("body")


def find_request_group_id(content: str) -> str:
    group_pattern = re.compile(r"(?P<id>[A-F0-9]{24}) /\* Networking \*/ = \{\n(?P<body>[\s\S]*?)\n\s*\};", re.MULTILINE)
    for match in group_pattern.finditer(content):
        body = match.group("body")
        if "HostPath.swift" not in body:
            continue
        children_match = re.search(r"children = \(\n(?P<children>[\s\S]*?)\n\s*\);", body)
        if not children_match:
            continue
        children = children_match.group("children")
        request_match = re.search(r"([A-F0-9]{24}) /\* Request \*/", children)
        if request_match:
            return request_match.group(1)
    die("Request group under Swift Networking not found in project.pbxproj")


def update_request_group_children(content: str, request_group_id: str, file_ref_id: str, class_filename: str) -> str:
    body, start, end = find_group_block(content, request_group_id, "Request")
    children_match = re.search(r"children = \(\n(?P<children>[\s\S]*?)\n\s*\);", body)
    if not children_match:
        die("failed to locate Request group children")
    children = children_match.group("children")
    if class_filename in children:
        return content

    indent = "\t\t\t\t"
    new_child_line = f"{indent}{file_ref_id} /* {class_filename} */,"
    new_children = children + "\n" + new_child_line

    new_body = body[: children_match.start("children")] + new_children + body[children_match.end("children") :]
    return content[:start] + new_body + content[end:]


def update_pbxproj(pbxproj_file: Path, class_filename: str) -> None:
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

    request_group_id = find_request_group_id(content)
    content = update_request_group_children(content, request_group_id, file_ref_id, class_filename)

    sources_insert_needle = "\t\t\t\t2ABA3CE027EDB030006B1E7E /* HostPath.swift in Sources */,"
    sources_line = f"\t\t\t\t{build_file_id} /* {class_filename} in Sources */,\n"
    content = insert_after_line(content, sources_insert_needle, sources_line)

    pbxproj_file.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Swift request + update HostPath.swift and project.pbxproj.")
    parser.add_argument("--method", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--params", default="")
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
    swift_source = build_request_class(class_name, host_name, path_name, params, args.method)

    if args.output_mode == "print":
        print(swift_source)
        return

    request_dir.mkdir(parents=True, exist_ok=True)
    out_file = request_dir / f"{class_name}.swift"
    out_file.write_text(swift_source, encoding="utf-8")

    if args.output_mode == "full":
        update_pbxproj(pbxproj_file, out_file.name)


if __name__ == "__main__":
    main()
