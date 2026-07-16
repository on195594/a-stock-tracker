# MILESTONE-004 SWS strict-TLS diagnostic

Authorization ID: `qualitative-v2-m4-sws-tls-diagnostic-2026-07-16-01`
Diagnostic ID: `m4-sws-tls-diagnostic-20260716-01`
Authorized window: `2026-07-16T17:30:00+08:00` through `2026-07-16T19:00:00+08:00`
Executed at: `2026-07-16T17:44:22.084000+08:00`
Status: **one handshake completed; strict verification failed; no retry**

## Authorization

The user approved the exact proposal in the controlling conversation:

> 批准上述 SWS strict-TLS 诊断授权提案。

The scope permitted exactly one TLS handshake to `www.swsresearch.com:443` with matching SNI, the installed Certifi
CA bundle, certificate verification enabled, and `verify_return_error`. It prohibited HTTP application data, retries,
capture calls, Tushare calls, Reviewer/model execution, database access, and production paths.

## Result

The handshake negotiated TLS 1.3 with `TLS_AES_256_GCM_SHA384`, then exited with code 1 because certificate
verification returned code 20: `unable to get local issuer certificate`. The 3,868-byte diagnostic output has SHA-256
`5a805b70e95492d7101e6fc3de0511fdadedea06861df46b114b3abc09cd45c5`.

The server sent exactly one certificate:

- Subject: `C=CN, ST=上海市, O=上海申银万国证券研究所有限公司, CN=*.swsresearch.com`
- Issuer: `C=US, O=DigiCert, Inc., CN=GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1`
- Validity: `2026-05-12T00:00:00Z` through `2026-11-26T23:59:59Z`
- SAN: `*.swsresearch.com`, `swsresearch.com`
- Serial: `0A93E8B6F9263473C9401874508C5370`
- SHA-256 fingerprint: `C7:23:9B:77:40:85:98:56:E2:63:4F:DD:B5:86:3A:EB:AC:F0:C0:F9:F5:71:E2:D8:C0:F6:77:C9:EC:E1:63:10`
- CA Issuers AIA: `http://cacerts.digicert.cn/GeoTrustG2TLSCNRSA4096SHA2562022CA1.crt`

The leaf certificate is currently valid and covers the requested hostname, but the server did not send the named
intermediate certificate. This explains both the strict diagnostic failure and the capture adapter's `SSLError`.

## Disposition

Do not disable verification, accept AKShare's upstream `verify=False`, fetch the AIA over unapproved HTTP, or retry the
same capture configuration. Safe unblocking requires one of:

1. the SWS endpoint begins serving a complete, strictly verifiable chain;
2. a separately authorized, provenance-bearing official intermediate certificate is obtained, pinned, and validated
   offline before another separately authorized TLS handshake; or
3. the required SWS response snapshots are supplied as a provenance-bearing static package.

No HTTP request, provider-data request, token access, production database access, or Reviewer/model execution occurred.

## Derived HTTPS certificate fetch

The user separately permitted exactly one HTTPS GET to the scheme-substituted AIA address
`https://cacerts.digicert.cn/GeoTrustG2TLSCNRSA4096SHA2562022CA1.crt`, with redirects disabled. At
`2026-07-16T17:48:59+08:00`, curl returned TLS alert `handshake failure` (exit code 35). No certificate bytes were
written, no alternate URL was attempted, and no retry occurred.
