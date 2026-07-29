use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Component, Path, PathBuf},
};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine};
use ed25519_dalek::{pkcs8::DecodePublicKey, Signature, Verifier, VerifyingKey};
use serde_json::Value;
use sha2::{Digest, Sha256};

#[cfg(any(target_os = "macos", test))]
// `security` exits with errSecItemNotFound (-25300) truncated to the Unix status byte.
const MACOS_KEYCHAIN_ITEM_NOT_FOUND_EXIT_CODE: i32 = 44;

#[cfg(any(target_os = "macos", test))]
fn macos_keychain_item_is_missing(exit_code: Option<i32>) -> bool {
    exit_code == Some(MACOS_KEYCHAIN_ITEM_NOT_FOUND_EXIT_CODE)
}

const WINDOWS_RESERVED: &[&str] = &[
    "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
    "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
];

pub fn safe_relative_path(value: &str, label: &str) -> Result<PathBuf, String> {
    if value.is_empty()
        || value.contains('\\')
        || value.starts_with('/')
        || (value.len() >= 2
            && value.as_bytes()[0].is_ascii_alphabetic()
            && value.as_bytes()[1] == b':')
    {
        return Err(format!("{label}必须是 POSIX 相对路径：{value}"));
    }

    let mut path = PathBuf::new();
    for segment in value.split('/') {
        let lowered = segment.to_ascii_lowercase();
        let reserved_base = lowered.split('.').next().unwrap_or_default();
        let has_windows_unsafe = segment
            .chars()
            .any(|character| character <= '\u{1f}' || r#":<>\"|?*"#.contains(character));
        if segment.is_empty()
            || segment == "."
            || segment == ".."
            || has_windows_unsafe
            || WINDOWS_RESERVED.contains(&reserved_base)
            || segment.ends_with(['.', ' '])
        {
            return Err(format!("{label}包含非法路径段：{value}"));
        }
        path.push(segment);
    }
    if path.components().any(|component| {
        matches!(
            component,
            Component::RootDir | Component::Prefix(_) | Component::ParentDir
        )
    }) {
        return Err(format!("{label}路径越界：{value}"));
    }
    Ok(path)
}

pub fn canonical_json(value: &Value) -> Result<String, String> {
    fn normalize(value: &Value) -> Result<String, String> {
        match value {
            Value::Null => Ok("null".to_string()),
            Value::Bool(value) => Ok(if *value { "true" } else { "false" }.to_string()),
            Value::Number(value) => Ok(value.to_string()),
            Value::String(value) => serde_json::to_string(value).map_err(|error| error.to_string()),
            Value::Array(values) => {
                let mut output = String::from("[");
                for (index, value) in values.iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    output.push_str(&normalize(value)?);
                }
                output.push(']');
                Ok(output)
            }
            Value::Object(values) => {
                let sorted: BTreeMap<&String, &Value> = values.iter().collect();
                let mut output = String::from("{");
                for (index, (key, value)) in sorted.iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    output
                        .push_str(&serde_json::to_string(key).map_err(|error| error.to_string())?);
                    output.push(':');
                    output.push_str(&normalize(value)?);
                }
                output.push('}');
                Ok(output)
            }
        }
    }

    normalize(value)
}

pub fn sha256_hex(bytes: impl AsRef<[u8]>) -> String {
    hex::encode(Sha256::digest(bytes.as_ref()))
}

pub fn verify_envelope_signature(envelope: &Value, trust_store: &Value) -> Result<Value, String> {
    let signature = envelope
        .get("signature")
        .and_then(Value::as_object)
        .ok_or_else(|| "插件内容没有有效的 Ed25519 签名".to_string())?;
    if signature.get("algorithm").and_then(Value::as_str) != Some("ed25519") {
        return Err("插件内容没有有效的 Ed25519 签名".to_string());
    }
    let key_id = signature
        .get("keyId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "插件签名缺少 keyId".to_string())?;
    let signature_value = signature
        .get("value")
        .and_then(Value::as_str)
        .ok_or_else(|| "插件签名缺少签名值".to_string())?;
    let key = trust_store
        .get("keys")
        .and_then(Value::as_array)
        .and_then(|keys| {
            keys.iter()
                .find(|key| key.get("id").and_then(Value::as_str) == Some(key_id))
        })
        .ok_or_else(|| format!("签名密钥不受信任：{key_id}"))?;
    if key.get("algorithm").and_then(Value::as_str) != Some("ed25519") {
        return Err(format!("签名密钥不受信任：{key_id}"));
    }
    let public_key = key
        .get("publicKey")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("签名密钥不受信任：{key_id}"))?;

    let mut unsigned = envelope.clone();
    unsigned
        .as_object_mut()
        .ok_or_else(|| "签名内容必须是对象".to_string())?
        .remove("signature");
    let payload = canonical_json(&unsigned)?;
    let verifying_key = VerifyingKey::from_public_key_pem(public_key)
        .map_err(|_| format!("签名密钥不受信任：{key_id}"))?;
    let signature_bytes = BASE64
        .decode(signature_value)
        .map_err(|_| "插件签名不是有效 Base64".to_string())?;
    let signature =
        Signature::from_slice(&signature_bytes).map_err(|_| "插件签名长度无效".to_string())?;
    verifying_key
        .verify(payload.as_bytes(), &signature)
        .map_err(|_| "插件签名校验失败，文件可能已被篡改".to_string())?;

    Ok(serde_json::json!({
        "keyId": key_id,
        "publisher": key.get("publisher").and_then(Value::as_str).unwrap_or("")
    }))
}

pub fn read_json(path: &Path) -> Result<Value, String> {
    let content = fs::read_to_string(path)
        .map_err(|error| format!("读取 {} 失败：{error}", path.display()))?;
    serde_json::from_str(&content).map_err(|error| format!("解析 {} 失败：{error}", path.display()))
}

pub fn write_json_atomic(path: &Path, value: &Value) -> Result<(), String> {
    let content = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
    write_private_atomic(path, content.as_bytes())
}

pub fn write_private_atomic(path: &Path, content: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("路径没有父目录：{}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("wandao");
    let temporary = parent.join(format!(
        ".{name}.{}.{}.tmp",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default()
    ));

    let result = (|| {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(&temporary)
            .map_err(|error| error.to_string())?;
        file.write_all(content).map_err(|error| error.to_string())?;
        file.sync_all().map_err(|error| error.to_string())?;
        drop(file);
        replace_file_atomic(&temporary, path)?;
        Ok(())
    })();

    if temporary.exists() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(target_os = "windows")]
fn replace_file_atomic(source: &Path, target: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let target: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            target.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error().to_string())
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
fn replace_file_atomic(source: &Path, target: &Path) -> Result<(), String> {
    fs::rename(source, target).map_err(|error| error.to_string())
}

#[cfg(target_os = "windows")]
pub fn protect_bytes(plain: &[u8]) -> Result<Vec<u8>, String> {
    use std::{ptr, slice};
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::Cryptography::{CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB},
    };

    let input = CRYPT_INTEGER_BLOB {
        cbData: plain.len() as u32,
        pbData: plain.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };
    let ok = unsafe {
        CryptProtectData(
            &input,
            ptr::null(),
            ptr::null(),
            ptr::null_mut(),
            ptr::null_mut(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if ok == 0 {
        return Err(format!(
            "DPAPI 加密失败：{}",
            std::io::Error::last_os_error()
        ));
    }
    let encrypted =
        unsafe { slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
    unsafe {
        LocalFree(output.pbData.cast());
    }
    Ok(encrypted)
}

#[cfg(target_os = "windows")]
pub fn unprotect_bytes(encrypted: &[u8]) -> Result<Vec<u8>, String> {
    use std::{ptr, slice};
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::Cryptography::{
            CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
        },
    };

    let input = CRYPT_INTEGER_BLOB {
        cbData: encrypted.len() as u32,
        pbData: encrypted.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };
    let ok = unsafe {
        CryptUnprotectData(
            &input,
            ptr::null_mut(),
            ptr::null(),
            ptr::null_mut(),
            ptr::null_mut(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if ok == 0 {
        return Err(format!(
            "DPAPI 解密失败：{}",
            std::io::Error::last_os_error()
        ));
    }
    let plain = unsafe { slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
    unsafe {
        LocalFree(output.pbData.cast());
    }
    Ok(plain)
}

#[cfg(target_os = "windows")]
pub fn unprotect_bytes_for_user_data(
    encrypted: &[u8],
    user_data: &Path,
) -> Result<Vec<u8>, String> {
    use aes_gcm::{
        aead::{Aead, KeyInit},
        Aes256Gcm, Nonce,
    };

    const V10_PREFIX: &[u8] = b"v10";
    const NONCE_LEN: usize = 12;
    const TAG_LEN: usize = 16;
    const DPAPI_PREFIX: &[u8] = b"DPAPI";

    if !encrypted.starts_with(V10_PREFIX) {
        return unprotect_bytes(encrypted);
    }
    if encrypted.len() < V10_PREFIX.len() + NONCE_LEN + TAG_LEN {
        return Err("Electron 安全存储数据格式无效".to_string());
    }

    let local_state = fs::read(user_data.join("Local State"))
        .map_err(|_| "无法读取 Electron 安全存储配置".to_string())?;
    let local_state: Value = serde_json::from_slice(&local_state)
        .map_err(|_| "Electron 安全存储配置格式无效".to_string())?;
    let encoded_key = local_state
        .get("os_crypt")
        .and_then(|value| value.get("encrypted_key"))
        .and_then(Value::as_str)
        .ok_or_else(|| "Electron 安全存储配置缺少密钥".to_string())?;
    let wrapped_key = BASE64
        .decode(encoded_key)
        .map_err(|_| "Electron 安全存储密钥格式无效".to_string())?;
    let protected_key = wrapped_key
        .strip_prefix(DPAPI_PREFIX)
        .ok_or_else(|| "Electron 安全存储密钥格式无效".to_string())?;
    let key =
        unprotect_bytes(protected_key).map_err(|_| "无法解锁 Electron 安全存储密钥".to_string())?;
    let cipher =
        Aes256Gcm::new_from_slice(&key).map_err(|_| "Electron 安全存储密钥长度无效".to_string())?;

    let nonce_start = V10_PREFIX.len();
    let ciphertext_start = nonce_start + NONCE_LEN;
    cipher
        .decrypt(
            Nonce::from_slice(&encrypted[nonce_start..ciphertext_start]),
            &encrypted[ciphertext_start..],
        )
        .map_err(|_| "Electron 安全存储数据解密失败".to_string())
}

#[cfg(not(target_os = "windows"))]
pub fn protect_bytes(plain: &[u8]) -> Result<Vec<u8>, String> {
    // macOS support uses the system Keychain through the `security` utility.
    // The AES-v10 implementation lives behind this small portable wrapper.
    macos_safe_storage::protect(plain)
}

#[cfg(not(target_os = "windows"))]
pub fn unprotect_bytes(encrypted: &[u8]) -> Result<Vec<u8>, String> {
    macos_safe_storage::unprotect(encrypted)
}

#[cfg(not(target_os = "windows"))]
pub fn unprotect_bytes_for_user_data(
    encrypted: &[u8],
    _user_data: &Path,
) -> Result<Vec<u8>, String> {
    unprotect_bytes(encrypted)
}

#[cfg(not(target_os = "windows"))]
mod macos_safe_storage {
    #[cfg(target_os = "macos")]
    use super::macos_keychain_item_is_missing;
    #[cfg(target_os = "macos")]
    use std::process::Command;

    #[cfg(target_os = "macos")]
    use aes::Aes128;
    #[cfg(target_os = "macos")]
    use cbc::{
        cipher::{block_padding::Pkcs7, BlockDecryptMut, BlockEncryptMut, KeyIvInit},
        Decryptor, Encryptor,
    };
    #[cfg(target_os = "macos")]
    use pbkdf2::pbkdf2_hmac;
    #[cfg(target_os = "macos")]
    use sha1::Sha1;

    #[cfg(target_os = "macos")]
    const SERVICES: &[(&str, &str)] = &[
        ("Wandao Safe Storage", "Wandao"),
        ("wandao Safe Storage", "wandao"),
        ("万能导 Wandao Safe Storage", "万能导 Wandao"),
    ];

    #[cfg(target_os = "macos")]
    fn password() -> Result<String, String> {
        for (service, account) in SERVICES {
            let output = Command::new("/usr/bin/security")
                .args(["find-generic-password", "-w", "-s", service, "-a", account])
                .output()
                .map_err(|error| format!("无法访问 macOS 钥匙串：{error}"))?;
            if output.status.success() {
                let password = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if password.is_empty() {
                    return Err(format!(
                        "macOS 钥匙串中的 {service:?} 密钥为空，已拒绝使用。"
                    ));
                }
                return Ok(password);
            }
            if !macos_keychain_item_is_missing(output.status.code()) {
                let details = String::from_utf8_lossy(&output.stderr).trim().to_string();
                return Err(if details.is_empty() {
                    format!(
                        "无法读取 macOS 钥匙串中的 {service:?} 密钥（状态 {}）；为避免覆盖历史任务密钥，已取消操作。",
                        output.status
                    )
                } else {
                    format!(
                        "无法读取 macOS 钥匙串中的 {service:?} 密钥：{details}；为避免覆盖历史任务密钥，已取消操作。"
                    )
                });
            }
        }
        // A fresh Tauri install has no Chromium-created key yet. Create the
        // same service/account pair and v10-compatible secret. Do not use -U:
        // a lookup/create race must fail instead of replacing historical data.
        let generated = format!(
            "{}{}",
            uuid::Uuid::new_v4().simple(),
            uuid::Uuid::new_v4().simple()
        );
        let (service, account) = SERVICES[0];
        let status = Command::new("/usr/bin/security")
            .args([
                "add-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
                &generated,
            ])
            .status()
            .map_err(|error| format!("无法写入 macOS 钥匙串：{error}"))?;
        if status.success() {
            Ok(generated)
        } else {
            Err("macOS 钥匙串无法创建 Wandao Safe Storage 密钥。".to_string())
        }
    }

    #[cfg(target_os = "macos")]
    fn key() -> Result<[u8; 16], String> {
        let mut key = [0_u8; 16];
        pbkdf2_hmac::<Sha1>(password()?.as_bytes(), b"saltysalt", 1003, &mut key);
        Ok(key)
    }

    #[cfg(target_os = "macos")]
    pub fn protect(plain: &[u8]) -> Result<Vec<u8>, String> {
        let key = key()?;
        let iv = [b' '; 16];
        let mut buffer = plain.to_vec();
        let original_len = buffer.len();
        buffer.resize(original_len + 16, 0);
        let encrypted = Encryptor::<Aes128>::new((&key).into(), (&iv).into())
            .encrypt_padded_mut::<Pkcs7>(&mut buffer, original_len)
            .map_err(|_| "macOS 安全存储加密失败".to_string())?;
        let mut output = b"v10".to_vec();
        output.extend_from_slice(encrypted);
        Ok(output)
    }

    #[cfg(target_os = "macos")]
    pub fn unprotect(encrypted: &[u8]) -> Result<Vec<u8>, String> {
        if !encrypted.starts_with(b"v10") {
            return Err("不支持的 macOS 安全存储格式".to_string());
        }
        let key = key()?;
        let iv = [b' '; 16];
        let mut buffer = encrypted[3..].to_vec();
        Decryptor::<Aes128>::new((&key).into(), (&iv).into())
            .decrypt_padded_mut::<Pkcs7>(&mut buffer)
            .map(|plain| plain.to_vec())
            .map_err(|_| "macOS 任务参数解密失败".to_string())
    }

    #[cfg(not(target_os = "macos"))]
    pub fn protect(_plain: &[u8]) -> Result<Vec<u8>, String> {
        Err("当前系统不支持安全存储".to_string())
    }

    #[cfg(not(target_os = "macos"))]
    pub fn unprotect(_encrypted: &[u8]) -> Result<Vec<u8>, String> {
        Err("当前系统不支持安全存储".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_json_sorts_nested_object_keys() {
        let input = serde_json::json!({"z": 1, "a": {"d": 4, "b": 2}, "items": [{"y": 2, "x": 1}]});
        assert_eq!(
            canonical_json(&input).unwrap(),
            r#"{"a":{"b":2,"d":4},"items":[{"x":1,"y":2}],"z":1}"#
        );
    }

    #[test]
    fn safe_relative_paths_match_plugin_v1_rules() {
        assert_eq!(
            safe_relative_path("providers/demo/provider.json", "Provider").unwrap(),
            PathBuf::from("providers/demo/provider.json")
        );
        for invalid in [
            "../provider.json",
            "providers\\demo.py",
            "/root/demo.py",
            "C:/demo.py",
            "providers/CON/file.py",
            "providers/trailing./file.py",
        ] {
            assert!(
                safe_relative_path(invalid, "Provider").is_err(),
                "{invalid}"
            );
        }
    }

    #[test]
    fn keychain_creation_is_only_allowed_after_item_not_found() {
        assert!(macos_keychain_item_is_missing(Some(44)));
        for status in [None, Some(0), Some(1), Some(36), Some(45)] {
            assert!(!macos_keychain_item_is_missing(status), "{status:?}");
        }
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn unprotects_synthetic_electron_v10_payload() {
        use aes_gcm::{
            aead::{Aead, KeyInit},
            Aes256Gcm, Nonce,
        };

        struct TemporaryUserData(PathBuf);

        impl Drop for TemporaryUserData {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }

        let user_data = TemporaryUserData(
            std::env::temp_dir().join(format!("wandao-safe-storage-test-{}", uuid::Uuid::new_v4())),
        );
        fs::create_dir_all(&user_data.0).unwrap();

        let key = [0x42_u8; 32];
        let mut wrapped_key = b"DPAPI".to_vec();
        wrapped_key.extend_from_slice(&protect_bytes(&key).unwrap());
        let local_state = serde_json::json!({
            "os_crypt": {
                "encrypted_key": BASE64.encode(wrapped_key)
            }
        });
        fs::write(
            user_data.0.join("Local State"),
            serde_json::to_vec(&local_state).unwrap(),
        )
        .unwrap();

        let plain = b"synthetic Electron safeStorage payload";
        let nonce = [0x24_u8; 12];
        let cipher = Aes256Gcm::new_from_slice(&key).unwrap();
        let ciphertext = cipher
            .encrypt(Nonce::from_slice(&nonce), plain.as_ref())
            .unwrap();
        let mut encrypted = b"v10".to_vec();
        encrypted.extend_from_slice(&nonce);
        encrypted.extend_from_slice(&ciphertext);

        assert_eq!(
            unprotect_bytes_for_user_data(&encrypted, &user_data.0).unwrap(),
            plain
        );

        let legacy_encrypted = protect_bytes(plain).unwrap();
        assert_eq!(
            unprotect_bytes_for_user_data(&legacy_encrypted, &user_data.0).unwrap(),
            plain
        );
    }
}
