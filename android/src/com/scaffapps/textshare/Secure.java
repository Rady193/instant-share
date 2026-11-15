package com.scaffapps.textshare;

import java.io.IOException;
import java.math.BigInteger;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.Certificate;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Keeping strangers out (Instant Share 1.2), the phone's side. Plain Java, no Android parts, so it can be checked on
 * a computer against the real server.
 *
 * Everything to the computer that keeps the history is encrypted (https) with that computer's own certificate.
 * The phone joins it once with a pairing code, checked with SPAKE2: the code never crosses the network, and the
 * pairing is bound to the computer's key as the phone saw it, so a computer in the middle cannot take part. From
 * then on the phone accepts only that key (the "pin") and sends its own device key with every request.
 */
final class Secure {
    static final BigInteger P = new BigInteger("ffffffffffffffffc90fdaa22168c234c4c6628b80dc1cd129024e088a67cc74020bbea63b139b22514a08798e3404ddef9519b3cd3a431b302b0a6df25f14374fe1356d6d51c245e485b576625e7ec6f44c42e9a637ed6b0bff5cb6f406b7edee386bfb5a899fa5ae9f24117c4b1fe649286651ece45b3dc2007cb8a163bf0598da48361c55d39a69163fa8fd24cf5f83655d23dca3ad961c62f356208552bb9ed529077096966d670c354e4abc9804f1746c08ca18217c32905e462e36ce3be39e772c180e86039b2783a2ec07a28fb5c55df06f4c52c9de2bcbf6955817183995497cea956ae515d2261898fa051015728e5a8aacaa68ffffffffffffffff", 16);
    static final BigInteger Q = P.subtract(BigInteger.ONE).shiftRight(1);
    static final BigInteger G = BigInteger.valueOf(4);
    static final BigInteger M = new BigInteger("b6547da67f441a3cd0dba0b7cc8555c4d454d831c8133241fe5a1647b1a94690c7160b767392d3ac15514fe7d96043df0b732a563f66a25384cf58a81f2e3e6dbc3c1d8d8a3fb706c30648fbf75b8af8a7ef320ffe007237c6a0609067ac5a900d3f30062a5afc6187293191e05d57d41700fb70c5c0f8f3dc0aeab0d92d0c307f6675fbdb68f8f01374bee7ca50f1925435239058bb5a1d579e7e1c7cfbba454352b2cb775bbca1780027afaf262831f1c81c40d8809dfc5b029485bfb4981d4687ce1b03c951e0bcd0121582f6df5fa719e244c53b7bc287c5b035055794ca9bf35b6f2f73da1d5a138e60ae71beca12e3b79352c19a67eb09671ca8b7067c", 16);
    static final BigInteger N = new BigInteger("128ab10fd9983b48b4f6235da3c4ed768c9e9502839e3a65e9a7e03fc5e0c4497cc9e3ce31b988f8e4877203c7f1b84a85d20499767e4f3a3a797dd5e14f39b4dd86475dc7d142360f1a469ef5d8e06b02e000c0e9c4747f82e996e862d9410edd1a0ef48514173afcdc8fa27c9041561a3e6e8a62a452969cb96d2c139a4743f6ea59d278aa061fda433ab52c584c20f81098814fa43d0d1e1a5c9c22dbfd795b9b07717d99bf488a38e1fa051560f73ef81ab14ba2954e13de22dc8f3aa96b2d5436f4acd8f1260acd5dd633f3be338ff7618e957179df4c218413186727a097b702c49860012b602d6a09e2c81a30ebe77a0a3b060a81f30793c21e24c7ab", 16);
    private static final SecureRandom RANDOM = new SecureRandom();

    private Secure() {}

    static byte[] sha256(byte[]... parts) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            for (byte[] p : parts) md.update(p);
            return md.digest();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x & 0xff));
        return s.toString();
    }

    static boolean same(String a, String b) {
        return a != null && b != null && MessageDigest.isEqual(a.getBytes(StandardCharsets.US_ASCII), b.getBytes(StandardCharsets.US_ASCII));
    }

    /** What the phone remembers about the computer: the SHA-256 of its public key. */
    static String pinOf(Certificate cert) {
        return hex(sha256(cert.getPublicKey().getEncoded()));
    }

    /** TLS that accepts exactly the computer with this pin; with none (only while joining), any, noting which. */
    static SSLSocketFactory pinned(final String pin, final String[] seen) throws Exception {
        TrustManager tm = new X509TrustManager() {
            @Override
            public void checkClientTrusted(X509Certificate[] chain, String auth) throws CertificateException {
                throw new CertificateException("not for clients");
            }

            @Override
            public void checkServerTrusted(X509Certificate[] chain, String auth) throws CertificateException {
                if (chain == null || chain.length == 0) throw new CertificateException("no certificate");
                String p = pinOf(chain[0]);
                if (seen != null) seen[0] = p;
                if (pin != null && !same(p, pin)) throw new CertificateException("not the computer this phone joined");
            }

            @Override
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        };
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[]{tm}, RANDOM);
        return ctx.getSocketFactory();
    }

    /** A connection to the computer that keeps the history: https with the pin, or http (only to find a 1.1). */
    static HttpURLConnection open(String url, int readTimeoutMs, String pin, String[] seen) throws Exception {
        HttpURLConnection con = (HttpURLConnection) URI.create(url).toURL().openConnection();
        if (con instanceof HttpsURLConnection) {
            HttpsURLConnection s = (HttpsURLConnection) con;
            s.setSSLSocketFactory(pinned(pin, seen));
            s.setHostnameVerifier((host, session) -> true);   // the pin is the check: names and addresses change
        }
        con.setConnectTimeout(8000);
        con.setReadTimeout(readTimeoutMs);
        con.setRequestProperty("X-TextShare", "1");
        return con;
    }

    // ---- SPAKE2

    static final class Start {
        final BigInteger w, x, T;
        Start(BigInteger w, BigInteger x, BigInteger T) { this.w = w; this.x = x; this.T = T; }
    }

    static BigInteger codeSecret(String code) {
        String digits = code.replaceAll("\\D", "");
        return new BigInteger(1, sha256(("InstantShare pairing code " + digits).getBytes(StandardCharsets.US_ASCII))).mod(Q);
    }

    static Start start(String code) {
        BigInteger w = codeSecret(code);
        BigInteger x = new BigInteger(Q.bitLength() + 64, RANDOM).mod(Q.subtract(BigInteger.ONE)).add(BigInteger.ONE);
        return new Start(w, x, G.modPow(x, P).multiply(M.modPow(w, P)).mod(P));
    }

    static boolean inGroup(BigInteger v) {
        return v.compareTo(BigInteger.ONE) > 0 && v.compareTo(P.subtract(BigInteger.ONE)) < 0 && v.modPow(Q, P).equals(BigInteger.ONE);
    }

    /** The shared secret, from the computer's answer S and the pin of the key it showed. */
    static byte[] finish(Start s, BigInteger S, String pin) throws IOException {
        if (!inGroup(S)) throw new IOException("That was not a proper answer.");
        BigInteger K = S.multiply(N.modPow(Q.subtract(s.w), P)).mod(P).modPow(s.x, P);
        byte[][] parts = {fixed(s.T, 256), fixed(S, 256), fixed(K, 256), fixed(s.w, 32), (pin == null ? "" : pin).getBytes(StandardCharsets.US_ASCII)};
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            md.update("InstantShare-SPAKE2-v1".getBytes(StandardCharsets.US_ASCII));
            for (byte[] p : parts) {
                md.update(new byte[]{(byte) (p.length >>> 24), (byte) (p.length >>> 16), (byte) (p.length >>> 8), (byte) p.length});
                md.update(p);
            }
            return md.digest();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String confirm(byte[] secret, String who) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            return hex(mac.doFinal(who.getBytes(StandardCharsets.US_ASCII)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static byte[] fixed(BigInteger v, int n) {
        byte[] b = v.toByteArray();
        byte[] out = new byte[n];
        int take = Math.min(b.length, n);
        System.arraycopy(b, b.length - take, out, n - take, take);
        return out;
    }
}
