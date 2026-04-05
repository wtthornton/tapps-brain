# SQLCipher platform installation

This guide covers installing the system SQLCipher library and the `pysqlcipher3` Python binding on each supported platform. For encryption setup, key management, and migration workflows, see [sqlcipher.md](sqlcipher.md).

## Prerequisites

Install the Python extra first:

```bash
pip install tapps-brain[encryption]
# or
uv sync --extra encryption
```

`pysqlcipher3` compiles against the system SQLCipher library. If the system library is missing, the pip install may succeed but `import pysqlcipher3` will fail at runtime.

---

## macOS (Homebrew)

```bash
brew install sqlcipher

# pysqlcipher3 needs to find the Homebrew headers and lib:
export LDFLAGS="-L$(brew --prefix sqlcipher)/lib"
export CPPFLAGS="-I$(brew --prefix sqlcipher)/include"

pip install pysqlcipher3
```

On Apple Silicon (arm64), Homebrew installs to `/opt/homebrew`. On Intel, it installs to `/usr/local`. The `brew --prefix` commands handle both automatically.

**Verify:**

```bash
python -c "from pysqlcipher3 import dbapi2; print(dbapi2.connect(':memory:').execute('PRAGMA cipher_version').fetchone())"
```

---

## Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y libsqlcipher-dev sqlcipher

pip install pysqlcipher3
```

On Ubuntu 22.04+, `libsqlcipher-dev` provides both the shared library and headers. Older releases may require building SQLCipher from source.

**Verify:**

```bash
python -c "from pysqlcipher3 import dbapi2; print(dbapi2.connect(':memory:').execute('PRAGMA cipher_version').fetchone())"
```

---

## Windows

Windows does not have a system package manager for SQLCipher. Options:

1. **vcpkg** (recommended for C/C++ toolchain users):

   ```powershell
   vcpkg install sqlcipher:x64-windows
   ```

   Then set `LIB` and `INCLUDE` environment variables to point to the vcpkg install before running `pip install pysqlcipher3`.

2. **Pre-built wheels:** Check [PyPI](https://pypi.org/project/pysqlcipher3/#files) for a Windows wheel matching your Python version. If available, `pip install pysqlcipher3` works without a system library.

3. **Build from source:** Clone [sqlcipher/sqlcipher](https://github.com/sqlcipher/sqlcipher), build with MSVC or MinGW, then install `pysqlcipher3` with appropriate `LDFLAGS`/`CPPFLAGS`.

Windows builds are the most involved. If encryption is not required on Windows developer machines, consider using plain SQLite locally and enabling encryption only in deployment environments.

---

## Behavior when key is set but pysqlcipher3 is missing

If an encryption key is configured (`TAPPS_BRAIN_ENCRYPTION_KEY` environment variable or `encryption_key=` parameter) but the `pysqlcipher3` package is not installed, tapps-brain raises an `ImportError` at store open time with a message indicating that the `[encryption]` extra is required.

The store will **not** silently fall back to unencrypted SQLite when a key is explicitly provided. This is a deliberate safety measure to prevent accidentally storing sensitive data in plaintext.

**To resolve:** either install the `[encryption]` extra (`pip install tapps-brain[encryption]`) and the system SQLCipher library, or remove the encryption key from the environment/configuration.
