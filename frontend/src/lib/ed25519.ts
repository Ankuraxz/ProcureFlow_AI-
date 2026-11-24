/**
 * Real Browser WebCrypto Ed25519 Mandate Signer for AP2 (Agent Payments Protocol).
 *
 * - Generates extractable Ed25519 keypairs via `window.crypto.subtle.generateKey`
 * - Persists JWK private/public key material in `localStorage` under `procureflow_ed25519_${userId}`
 * - Exports raw 32-byte public keys as standard Base64 (`public_key_b64`)
 * - Computes live SHA-256 fingerprints over raw 32-byte public keys
 * - Signs UTF-8 canonical mandate payloads producing 64-byte Base64 Ed25519 signatures
 */

export interface StoredPersonaKeyRecord {
  privateJwk: JsonWebKey;
  publicJwk: JsonWebKey;
  publicKeyB64: string;
  fingerprint: string;
  createdAt: string;
}

export interface LoadedPersonaKeyPair {
  privateKey: CryptoKey;
  publicKey: CryptoKey;
  publicKeyB64: string;
  fingerprint: string;
  createdAt: string;
}

export const ED25519_STORAGE_PREFIX = 'procureflow_ed25519_';

export function getKeyStorageId(userId: string): string {
  return `${ED25519_STORAGE_PREFIX}${userId}`;
}

/**
 * Encodes an ArrayBuffer into standard RFC 4648 Base64 (`+`, `/`, `=`).
 */
export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary);
}

/**
 * Decodes a standard Base64 string into an ArrayBuffer.
 */
export function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const binary = window.atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * Computes a compact colon-delimited SHA-256 fingerprint of the 32-byte raw Ed25519 public key.
 */
export async function computePublicKeyFingerprint(rawPublicKey: ArrayBuffer): Promise<string> {
  const digest = await window.crypto.subtle.digest('SHA-256', rawPublicKey);
  const bytes = Array.from(new Uint8Array(digest).slice(0, 6));
  const hexPairs = bytes.map((b) => b.toString(16).padStart(2, '0')).join(':');
  return `ED25519:${hexPairs}`;
}

/**
 * Encodes a UTF-8 string into a standalone `ArrayBuffer` for strict TS 5.7+ `BufferSource` compatibility.
 */
export function utf8ToArrayBuffer(text: string): ArrayBuffer {
  const encoded = new TextEncoder().encode(text);
  return encoded.buffer.slice(
    encoded.byteOffset,
    encoded.byteOffset + encoded.byteLength,
  ) as ArrayBuffer;
}

/**
 * Computes a lowercase hex SHA-256 digest of a UTF-8 string using WebCrypto.
 */
export async function sha256Hex(payload: string): Promise<string> {
  const buf = utf8ToArrayBuffer(payload);
  const digest = await window.crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

function assertCryptoKeyPair(key: CryptoKeyPair | CryptoKey): CryptoKeyPair {
  if ('privateKey' in key && 'publicKey' in key) {
    return key;
  }
  throw new Error('WebCrypto Ed25519 generateKey did not return an asymmetric CryptoKeyPair.');
}

/**
 * Loads an existing Ed25519 keypair from `localStorage` (`procureflow_ed25519_${userId}`)
 * or generates and persists a fresh extractable Ed25519 keypair via WebCrypto.
 */
export async function getOrCreatePersonaEd25519Key(
  userId: string,
  forceRotate = false,
): Promise<LoadedPersonaKeyPair> {
  if (!window.crypto?.subtle) {
    throw new Error('Browser WebCrypto SubtleCrypto API is unavailable in this secure context.');
  }

  const storageKey = getKeyStorageId(userId);

  if (!forceRotate) {
    try {
      const rawStored = window.localStorage.getItem(storageKey);
      if (rawStored) {
        const parsed = JSON.parse(rawStored) as Partial<StoredPersonaKeyRecord>;
        if (parsed.privateJwk && parsed.publicJwk) {
          const privateKey = await window.crypto.subtle.importKey(
            'jwk',
            parsed.privateJwk,
            { name: 'Ed25519' },
            true,
            ['sign'],
          );
          const publicKey = await window.crypto.subtle.importKey(
            'jwk',
            parsed.publicJwk,
            { name: 'Ed25519' },
            true,
            ['verify'],
          );
          const rawPub = await window.crypto.subtle.exportKey('raw', publicKey);
          const publicKeyB64 = arrayBufferToBase64(rawPub);
          const fingerprint =
            parsed.fingerprint || (await computePublicKeyFingerprint(rawPub));
          const createdAt = parsed.createdAt || new Date().toISOString();

          return {
            privateKey,
            publicKey,
            publicKeyB64,
            fingerprint,
            createdAt,
          };
        }
      }
    } catch {
      // Corrupted or incompatible stored JWK; fall through to regenerate cleanly.
    }
  }

  const generated = await window.crypto.subtle.generateKey(
    { name: 'Ed25519' },
    true,
    ['sign', 'verify'],
  );
  const keyPair = assertCryptoKeyPair(generated);

  const [privateJwk, publicJwk, rawPublicKey] = await Promise.all([
    window.crypto.subtle.exportKey('jwk', keyPair.privateKey),
    window.crypto.subtle.exportKey('jwk', keyPair.publicKey),
    window.crypto.subtle.exportKey('raw', keyPair.publicKey),
  ]);

  if (rawPublicKey.byteLength !== 32) {
    throw new Error(
      `Expected 32-byte Ed25519 raw public key, got ${rawPublicKey.byteLength} bytes.`,
    );
  }

  const publicKeyB64 = arrayBufferToBase64(rawPublicKey);
  const fingerprint = await computePublicKeyFingerprint(rawPublicKey);
  const createdAt = new Date().toISOString();

  const record: StoredPersonaKeyRecord = {
    privateJwk,
    publicJwk,
    publicKeyB64,
    fingerprint,
    createdAt,
  };

  try {
    window.localStorage.setItem(storageKey, JSON.stringify(record));
  } catch {
    // Ignore storage quota errors in restricted incognito contexts; in-memory keys still work.
  }

  return {
    privateKey: keyPair.privateKey,
    publicKey: keyPair.publicKey,
    publicKeyB64,
    fingerprint,
    createdAt,
  };
}

/**
 * Signs a canonical AP2 mandate payload string encoded as UTF-8 using the persona's
 * WebCrypto Ed25519 private key and returns the 64-byte signature + 32-byte public key as Base64.
 */
export async function signCanonicalMandate(
  userId: string,
  canonicalPayload: string,
): Promise<{ signatureB64: string; publicKeyB64: string }> {
  const { privateKey, publicKey, publicKeyB64 } = await getOrCreatePersonaEd25519Key(userId);
  const bytes = utf8ToArrayBuffer(canonicalPayload);

  const signatureBuffer = await window.crypto.subtle.sign(
    { name: 'Ed25519' },
    privateKey,
    bytes,
  );

  if (signatureBuffer.byteLength !== 64) {
    throw new Error(
      `Expected 64-byte Ed25519 signature, received ${signatureBuffer.byteLength} bytes.`,
    );
  }

  const verifiedLocally = await window.crypto.subtle.verify(
    { name: 'Ed25519' },
    publicKey,
    signatureBuffer,
    bytes,
  );
  if (!verifiedLocally) {
    throw new Error('Local WebCrypto Ed25519 self-verification check failed.');
  }

  return {
    signatureB64: arrayBufferToBase64(signatureBuffer),
    publicKeyB64,
  };
}

/**
 * Verifies an Ed25519 Base64 signature over a UTF-8 canonical payload using WebCrypto.
 */
export async function verifyCanonicalMandateSignature(
  publicKeyB64: string,
  canonicalPayload: string,
  signatureB64: string,
): Promise<boolean> {
  try {
    const rawPubBuffer = base64ToArrayBuffer(publicKeyB64);
    const sigBuffer = base64ToArrayBuffer(signatureB64);
    const publicKey = await window.crypto.subtle.importKey(
      'raw',
      rawPubBuffer,
      { name: 'Ed25519' },
      false,
      ['verify'],
    );
    const payloadBytes = utf8ToArrayBuffer(canonicalPayload);
    return await window.crypto.subtle.verify(
      { name: 'Ed25519' },
      publicKey,
      sigBuffer,
      payloadBytes,
    );
  } catch {
    return false;
  }
}
