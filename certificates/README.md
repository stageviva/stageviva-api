# Apple verification root

AppleRootCA-G3.cer is the public DER certificate downloaded from:
https://www.apple.com/certificateauthority/AppleRootCA-G3.cer

SHA-256: 63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179

Used by Apple's official App Store Server Library to validate the certificate
chain of signed transactions and notifications. This is a public trust anchor,
not an account signing key. Account .p8 keys belong only in hosting secrets.
