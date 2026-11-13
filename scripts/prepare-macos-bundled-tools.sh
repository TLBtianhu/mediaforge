#!/usr/bin/env bash
set -euo pipefail

DEST_DIR="${1:-src-tauri/resources/bin}"
TARGET_ARCH="${PYMSS_MACOS_TOOLS_ARCH:-$(uname -m)}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script only supports macOS." >&2
  exit 1
fi

case "$TARGET_ARCH" in
  arm64)
    EXPECTED_MACHO_ARCH="arm64"
    FFMPEG_URL="${PYMSS_FFMPEG_URL:-https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffmpeg.zip}"
    FFMPEG_SHA256="${PYMSS_FFMPEG_SHA256:-c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924}"
    FFPROBE_URL="${PYMSS_FFPROBE_URL:-https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffprobe.zip}"
    FFPROBE_SHA256="${PYMSS_FFPROBE_SHA256:-fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6}"
    ARIA2_URL="${PYMSS_ARIA2_URL:-https://github.com/FerroDownload/aria2-static-builds/releases/download/v1.37.0/aria2c-1.37.0-macos-arm64}"
    ARIA2_SHA256="${PYMSS_ARIA2_SHA256:-47f4f54095e2bde1774c344dc376d1c84d92c4bcff90eed4117055091e197dd2}"
    ;;
  x86_64|amd64)
    EXPECTED_MACHO_ARCH="x86_64"
    FFMPEG_URL="${PYMSS_FFMPEG_URL:-https://ffmpeg.martin-riedl.de/download/macos/amd64/1789931006_9.0.2/ffmpeg.zip}"
    FFMPEG_SHA256="${PYMSS_FFMPEG_SHA256:-7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4}"
    FFPROBE_URL="${PYMSS_FFPROBE_URL:-https://ffmpeg.martin-riedl.de/download/macos/amd64/1789931006_9.0.2/ffprobe.zip}"
    FFPROBE_SHA256="${PYMSS_FFPROBE_SHA256:-2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd}"
    ARIA2_URL="${PYMSS_ARIA2_URL:-https://github.com/FerroDownload/aria2-static-builds/releases/download/v1.37.0/aria2c-1.37.0-macos-x64}"
    ARIA2_SHA256="${PYMSS_ARIA2_SHA256:-c96de7025d9c4fba2e2607a979d61d154a2ec66b110f6dedfbf9d78a3f6ab0ee}"
    ;;
  *)
    echo "Unsupported macOS architecture: $TARGET_ARCH" >&2
    exit 1
    ;;
esac

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
mkdir -p "$DEST_DIR/lib"

download_checked() {
  local url="$1"
  local expected_sha256="$2"
  local destination="$3"
  local actual_sha256

  curl --fail --location --silent --show-error \
    --retry 5 --retry-delay 2 --retry-all-errors \
    --output "$destination" "$url"
  actual_sha256="$(shasum -a 256 "$destination" | awk '{print $1}')"
  if [[ "$actual_sha256" != "$expected_sha256" ]]; then
    echo "Checksum mismatch for $url" >&2
    echo "Expected: $expected_sha256" >&2
    echo "Actual:   $actual_sha256" >&2
    exit 1
  fi
}

install_zip_binary() {
  local name="$1"
  local url="$2"
  local sha256="$3"
  local archive="$WORK_DIR/$name.zip"
  local extract_dir="$WORK_DIR/$name"
  local source

  download_checked "$url" "$sha256" "$archive"
  mkdir -p "$extract_dir"
  ditto -x -k "$archive" "$extract_dir"
  source="$(find "$extract_dir" -type f -name "$name" -print -quit)"
  if [[ -z "$source" ]]; then
    echo "$name was not found in $url" >&2
    exit 1
  fi
  install -m 755 "$source" "$DEST_DIR/$name"
}

install_direct_binary() {
  local name="$1"
  local url="$2"
  local sha256="$3"
  local source="$WORK_DIR/$name"

  download_checked "$url" "$sha256" "$source"
  install -m 755 "$source" "$DEST_DIR/$name"
}

is_system_dependency() {
  local dependency="$1"
  [[ "$dependency" == /usr/lib/* || "$dependency" == /System/Library/* ]]
}

is_homebrew_dependency() {
  local dependency="$1"
  [[ "$dependency" == /opt/homebrew/* || "$dependency" == /usr/local/* ]]
}

macho_dependencies() {
  local binary="$1"
  otool -l "$binary" | awk '
    $1 == "cmd" {
      load = ($2 == "LC_LOAD_DYLIB" || $2 == "LC_LOAD_WEAK_DYLIB" ||
              $2 == "LC_REEXPORT_DYLIB" || $2 == "LC_LAZY_LOAD_DYLIB" ||
              $2 == "LC_LOAD_UPWARD_DYLIB")
      next
    }
    load && $1 == "name" { print $2; load = 0 }
  '
}

macho_rpaths() {
  local binary="$1"
  otool -l "$binary" | awk '
    $1 == "cmd" && $2 == "LC_RPATH" { expect_path = 1; next }
    expect_path && $1 == "path" { print $2; expect_path = 0 }
  '
}

resolve_dependency() {
  local dependency="$1"
  local loader="$2"
  local base candidate prefix found

  if is_homebrew_dependency "$dependency" && [[ -f "$dependency" ]]; then
    printf '%s\n' "$dependency"
    return 0
  fi

  if [[ "$dependency" == @loader_path/* ]]; then
    candidate="$(dirname "$loader")/${dependency#@loader_path/}"
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  if [[ "$dependency" == @rpath/* ]]; then
    base="$(basename "$dependency")"
    for prefix in /opt/homebrew /usr/local; do
      [[ -d "$prefix" ]] || continue
      for candidate in "$prefix/lib/$base" "$prefix"/opt/*/lib/"$base"; do
        if [[ -f "$candidate" ]]; then
          printf '%s\n' "$candidate"
          return 0
        fi
      done
      found="$(find "$prefix/opt" -path "*/lib/$base" -type f -print -quit 2>/dev/null || true)"
      if [[ -n "$found" ]]; then
        printf '%s\n' "$found"
        return 0
      fi
    done
  fi

  return 1
}

declare -a pending_dependencies=()
declare -a known_dependency_sources=()
declare -a aria2_runtime_source_manifest=()
pending_dependency_count=0
pending_dependency_cursor=0
known_dependency_source_count=0
aria2_runtime_manifest_count=0

is_known_dependency_source() {
  local source="$1"
  local index

  # Bash 3.2 treats an explicitly declared empty array as unset under `set -u`.
  # Indexed iteration avoids expanding `${array[@]}` until an element exists.
  for ((index = 0; index < known_dependency_source_count; index += 1)); do
    if [[ "${known_dependency_sources[$index]}" == "$source" ]]; then
      return 0
    fi
  done
  for ((index = 0; index < pending_dependency_count; index += 1)); do
    if [[ "${pending_dependencies[$index]}" == "$source" ]]; then
      return 0
    fi
  done
  return 1
}

queue_dependency() {
  local dependency="$1"
  local loader="$2"
  local source

  if is_system_dependency "$dependency"; then
    return 0
  fi
  source="$(resolve_dependency "$dependency" "$loader" || true)"
  if [[ -z "$source" ]]; then
    echo "Unable to resolve non-system dependency for $loader: $dependency" >&2
    echo "Install the required aria2 runtime libraries before packaging." >&2
    exit 1
  fi
  if ! is_known_dependency_source "$source"; then
    pending_dependencies[$pending_dependency_count]="$source"
    pending_dependency_count=$((pending_dependency_count + 1))
  fi
}

bundle_aria2_dependencies() {
  local dependency source destination next_dependency

  while IFS= read -r dependency; do
    queue_dependency "$dependency" "$DEST_DIR/aria2c"
  done < <(macho_dependencies "$DEST_DIR/aria2c")

  while ((pending_dependency_cursor < pending_dependency_count)); do
    source="${pending_dependencies[$pending_dependency_cursor]}"
    pending_dependency_cursor=$((pending_dependency_cursor + 1))
    destination="$DEST_DIR/lib/$(basename "$source")"

    if [[ -f "$destination" ]] && ! cmp -s "$source" "$destination"; then
      echo "Bundled dependency name collision: $source and $destination" >&2
      exit 1
    fi
    if [[ ! -f "$destination" ]]; then
      cp -L "$source" "$destination"
      chmod u+w "$destination"
    fi
    known_dependency_sources[$known_dependency_source_count]="$source"
    known_dependency_source_count=$((known_dependency_source_count + 1))
    aria2_runtime_source_manifest[$aria2_runtime_manifest_count]="$(basename "$source") (source SHA-256: $(shasum -a 256 "$source" | awk '{print $1}'))"
    aria2_runtime_manifest_count=$((aria2_runtime_manifest_count + 1))

    while IFS= read -r next_dependency; do
      queue_dependency "$next_dependency" "$source"
    done < <(macho_dependencies "$source")
  done
}

patch_macho_dependencies() {
  local binary="$1"
  local dependency base replacement rpath
  base="$(basename "$binary")"

  if [[ "$binary" == "$DEST_DIR/lib/"*.dylib ]]; then
    install_name_tool -id "@loader_path/$base" "$binary"
  fi

  while IFS= read -r dependency; do
    if is_system_dependency "$dependency"; then
      continue
    fi
    if [[ "$binary" == "$DEST_DIR/lib/$base" && "$dependency" == "@loader_path/$base" ]]; then
      continue
    fi
    if [[ "$binary" == "$DEST_DIR/lib/"* ]]; then
      replacement="@loader_path/$(basename "$dependency")"
    else
      replacement="@loader_path/lib/$(basename "$dependency")"
    fi
    install_name_tool -change "$dependency" "$replacement" "$binary"
  done < <(macho_dependencies "$binary")

  # Homebrew bottles may retain absolute search paths even after every @rpath dependency has
  # been rewritten above. They are unnecessary in the app bundle and leak the build host path.
  while IFS= read -r rpath; do
    if is_homebrew_dependency "$rpath"; then
      install_name_tool -delete_rpath "$rpath" "$binary"
    fi
  done < <(macho_rpaths "$binary")
}

assert_portable_macho() {
  local binary="$1"
  local architectures dependency resolved rpath

  if ! file -b "$binary" | grep -q 'Mach-O'; then
    echo "Bundled tool is not a Mach-O executable: $binary" >&2
    file "$binary" >&2
    exit 1
  fi
  architectures="$(lipo -archs "$binary")"
  if ! tr ' ' '\n' <<<"$architectures" | grep -qx "$EXPECTED_MACHO_ARCH"; then
    echo "Bundled tool has the wrong architecture: $binary ($architectures)" >&2
    exit 1
  fi

  while IFS= read -r dependency; do
    if is_system_dependency "$dependency"; then
      continue
    fi
    if [[ "$dependency" != @loader_path/* ]]; then
      echo "Bundled tool still has a non-relocatable dependency: $binary" >&2
      echo "$dependency" >&2
      exit 1
    fi
    resolved="$(dirname "$binary")/${dependency#@loader_path/}"
    if [[ ! -f "$resolved" ]]; then
      echo "Bundled tool dependency is missing: $binary -> $dependency" >&2
      exit 1
    fi
  done < <(macho_dependencies "$binary")

  while IFS= read -r rpath; do
    if [[ "$rpath" == /opt/homebrew/* || "$rpath" == /usr/local/* ]]; then
      echo "Bundled tool still has a host-specific LC_RPATH: $binary" >&2
      echo "$rpath" >&2
      exit 1
    fi
  done < <(macho_rpaths "$binary")
}

install_zip_binary ffmpeg "$FFMPEG_URL" "$FFMPEG_SHA256"
install_zip_binary ffprobe "$FFPROBE_URL" "$FFPROBE_SHA256"
install_direct_binary aria2c "$ARIA2_URL" "$ARIA2_SHA256"
bundle_aria2_dependencies

while IFS= read -r -d '' binary; do
  if file -b "$binary" | grep -q 'Mach-O'; then
    patch_macho_dependencies "$binary"
  fi
done < <(find "$DEST_DIR" -type f -print0)

while IFS= read -r -d '' binary; do
  if file -b "$binary" | grep -q 'Mach-O'; then
    assert_portable_macho "$binary"
    xattr -d com.apple.quarantine "$binary" 2>/dev/null || true
    codesign --force --sign - --timestamp=none "$binary"
  fi
done < <(find "$DEST_DIR" -type f -print0)

"$DEST_DIR/ffmpeg" -version >/dev/null
"$DEST_DIR/ffprobe" -version >/dev/null
"$DEST_DIR/aria2c" --version >/dev/null

HOMEBREW_RUNTIME_VERSIONS="$(brew list --versions zlib expat sqlite c-ares | sed 's/^/  - /')"
if ((aria2_runtime_manifest_count == 0)); then
  echo "aria2c did not produce a bundled runtime dependency manifest." >&2
  exit 1
fi
ARIA2_RUNTIME_MANIFEST="$(
  for ((index = 0; index < aria2_runtime_manifest_count; index += 1)); do
    printf '  - %s\n' "${aria2_runtime_source_manifest[$index]}"
  done
)"

cat > "$DEST_DIR/THIRD_PARTY_TOOLS.txt" <<EOF
This directory contains self-contained command-line tools bundled for MediaForge macOS releases.

Bundled tools:
- FFmpeg / ffprobe
  Version: $("$DEST_DIR/ffmpeg" -version | head -n 1)
  Architecture: $EXPECTED_MACHO_ARCH
  Source: $FFMPEG_URL
  FFmpeg SHA-256: $FFMPEG_SHA256
  FFprobe SHA-256: $FFPROBE_SHA256
  Project: https://ffmpeg.org/
  License information: https://ffmpeg.org/legal.html

- aria2 / aria2c
  Version: $("$DEST_DIR/aria2c" --version | head -n 1)
  Architecture: $EXPECTED_MACHO_ARCH
  Source: $ARIA2_URL
  SHA-256: $ARIA2_SHA256
  Project: https://aria2.github.io/
  License information: https://github.com/aria2/aria2/blob/master/COPYING
  Runtime libraries: zlib, expat, sqlite, c-ares (bundled under lib/)
  Runtime library source: https://formulae.brew.sh/
  Homebrew formula versions:
$HOMEBREW_RUNTIME_VERSIONS
  Runtime library source manifest (before relinking and signing):
$ARIA2_RUNTIME_MANIFEST

The release build verifies each download by SHA-256 and rejects binaries that
contain host-specific dynamic-library paths. aria2c runtime libraries are copied
from Homebrew and rewritten to app-local @loader_path references.
EOF

echo "Prepared portable macOS tools in $DEST_DIR"
