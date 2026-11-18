package com.scaffapps.textshare;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.os.Build;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/**
 * Talking to the computer that keeps the history (the Instant Share server on the same network), and what this phone
 * remembers about it: its name and address, the pin of its key, and this phone's own device key from pairing.
 * Everything is encrypted and goes only to the computer with that pin (Secure); every request carries the key.
 */
final class Net {
    static final String PREFS = "textshare";
    static final int PORT = 8765, DISCOVERY_PORT = 8765;
    private static final byte[] ASK = "TEXTSHARE?".getBytes(StandardCharsets.US_ASCII);

    private Net() {}

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE); }

    /** Who this phone is to the others: made up the first time, kept for good. */
    static synchronized String deviceId(Context c) {
        String id = prefs(c).getString("device_id", null);
        if (id == null) {
            byte[] b = new byte[12];
            new SecureRandom().nextBytes(b);
            StringBuilder s = new StringBuilder();
            for (byte x : b) s.append(String.format("%02x", x & 0xff));
            id = s.toString();
            prefs(c).edit().putString("device_id", id).apply();
        }
        return id;
    }

    /** The name the others see: the one chosen in the page, else the phone's own name, else its model. */
    static String name(Context c) {
        String n = prefs(c).getString("name", null);
        if (n == null || n.trim().isEmpty()) n = Settings.Global.getString(c.getContentResolver(), Settings.Global.DEVICE_NAME);
        if (n == null || n.trim().isEmpty()) n = Build.MODEL;
        return n.trim();
    }

    /** https://address:port of the computer that keeps the history, or null before setup. */
    static String base(Context c) {
        SharedPreferences p = prefs(c);
        String host = p.getString("address", null);
        if (host == null) host = p.getString("host", null);
        if (host == null) return null;
        return "https://" + (host.contains(":") ? "[" + host + "]" : host) + ":" + p.getInt("port", PORT);
    }

    static String key(Context c) { return prefs(c).getString("key", null); }

    static String pin(Context c) { return prefs(c).getString("pin", null); }

    /** Set up and joined: it knows the computer, that computer's key, and has its own key. */
    static boolean paired(Context c) { return base(c) != null && key(c) != null && pin(c) != null; }

    /** The computer no longer knows this phone (removed there): it must join again. */
    static void forgetKey(Context c) { prefs(c).edit().remove("key").apply(); }

    static String hostName(Context c) { return prefs(c).getString("host", ""); }

    /** A request as this phone: encrypted, only to the paired computer, with this phone's key. */
    static HttpURLConnection open(Context c, String url, int readTimeoutMs) throws Exception {
        String pin = pin(c), key = key(c);
        if (pin == null || key == null) throw new IOException("This phone has not joined Instant Share yet");
        HttpURLConnection con = Secure.open(url, readTimeoutMs, pin, null);
        con.setRequestProperty("X-TextShare-Key", key);
        con.setRequestProperty("X-TextShare-Name", URLEncoder.encode(name(c), "UTF-8").replace("+", "%20"));
        con.setRequestProperty("X-TextShare-Kind", "app");
        return con;
    }

    /** Thrown when the computer answers that it does not know this phone (any more). */
    static final class Unpaired extends IOException {
        Unpaired() { super("This phone is not joined to Instant Share there (any more)"); }
    }

    static byte[] bytes(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
        in.close();
        return out.toByteArray();
    }

    /** GET a path (or a full address on the same computer) as this device: the answer's bytes, or an exception. */
    static byte[] get(Context c, String urlOrPath) throws Exception {
        String base = base(c);
        if (base == null) throw new java.io.IOException("Instant Share is not set up yet");
        String url = urlOrPath.startsWith("/") ? base + urlOrPath : urlOrPath;
        if (!url.startsWith(base + "/")) throw new java.io.IOException("not from the computer that keeps the history");
        HttpURLConnection con = open(c, url, 30000);
        try {
            if (con.getResponseCode() == 401) { forgetKey(c); throw new Unpaired(); }
            if (con.getResponseCode() != 200) throw new java.io.IOException("the computer answered " + con.getResponseCode());
            return bytes(con.getInputStream());
        } finally {
            con.disconnect();
        }
    }

    /** Instant Share's answer at host:port (it says its computer's name), or null. Encrypted; before 1.2 (plain
     *  http) the answer has "old": its version, so the phone can say to update that computer. */
    static JSONObject hello(String host, int port, int timeoutMs) {
        String where = (host.contains(":") ? "[" + host + "]" : host) + ":" + port;
        for (String scheme : new String[]{"https://", "http://"}) {
            HttpURLConnection con = null;
            try {
                con = Secure.open(scheme + where + "/api/hello", timeoutMs, null, null);
                con.setConnectTimeout(timeoutMs);
                if (con.getResponseCode() != 200) continue;
                JSONObject o = new JSONObject(new String(bytes(con.getInputStream()), StandardCharsets.UTF_8));
                if (!o.has("server")) continue;
                if (scheme.equals("http://")) o.put("old", o.optString("version", "1.0"));
                return o;
            } catch (Exception e) {
                // not there, or not this scheme
            } finally {
                if (con != null) con.disconnect();
            }
        }
        return null;
    }

    /** The computer set up here answers now, as itself (by its name; its key is checked when its page opens). */
    static boolean reachable(Context c) {
        SharedPreferences p = prefs(c);
        String host = p.getString("address", p.getString("host", null));
        if (host == null) return false;
        JSONObject h = hello(host, p.getInt("port", PORT), 3000);
        return h != null && !h.has("old") && h.optString("server").equalsIgnoreCase(hostName(c));
    }

    /** Join the computer set up in the preferences with a pairing code; on success its pin and this phone's key are
     *  kept. Throws IOException with a message for the user. */
    static void pair(Context c, String code) throws Exception {
        String base = base(c);
        if (base == null) throw new IOException("Choose the computer that keeps the history first.");
        String[] seen = new String[1];
        HttpURLConnection h = Secure.open(base + "/api/hello", 6000, null, seen);
        try { h.getResponseCode(); } finally { h.disconnect(); }
        String pin = seen[0];
        if (pin == null) throw new IOException("That computer did not show a key.");
        Secure.Start s = Secure.start(code);
        JSONObject a = post(base + "/api/pair", pin, new JSONObject().put("T", s.T.toString(16)).put("name", name(c))
                .put("kind", "app").put("id", deviceId(c)).put("pinned", true));
        byte[] secret = Secure.finish(s, new BigInteger(a.getString("S"), 16), pin);
        if (!Secure.same(a.optString("confirm"), Secure.confirm(secret, "server")))
            throw new IOException("That code is not right, or no longer valid. Check it, or make a new one, and try again.");
        JSONObject done = post(base + "/api/pair/finish", pin, new JSONObject().put("session", a.getString("session"))
                .put("confirm", Secure.confirm(secret, "client")));
        prefs(c).edit().putString("key", done.getString("key")).putString("pin", pin)
                .putString("device_id", done.getString("id")).apply();
    }

    private static JSONObject post(String url, String pin, JSONObject body) throws Exception {
        HttpURLConnection con = Secure.open(url, 20000, pin, null);
        try {
            con.setRequestMethod("POST");
            con.setDoOutput(true);
            con.setRequestProperty("Content-Type", "application/json");
            try (OutputStream o = con.getOutputStream()) { o.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
            int code = con.getResponseCode();
            InputStream in = code >= 400 ? con.getErrorStream() : con.getInputStream();
            JSONObject o = new JSONObject(in == null ? "{}" : new String(bytes(in), StandardCharsets.UTF_8));
            if (code >= 400) throw new IOException(o.optString("error", "The computer answered " + code + "."));
            return o;
        } finally {
            con.disconnect();
        }
    }

    /** The computers on this network running Instant Share: [{name, address, port}], asked by a broadcast. */
    static JSONArray discover(Context c, int ms) {
        Map<String, JSONObject> found = new LinkedHashMap<>();
        try (DatagramSocket s = new DatagramSocket()) {
            s.setBroadcast(true);
            Set<InetAddress> targets = new LinkedHashSet<>();
            targets.add(InetAddress.getByName("255.255.255.255"));
            targets.addAll(subnetBroadcasts(c));
            for (InetAddress t : targets) {
                try { s.send(new DatagramPacket(ASK, ASK.length, t, DISCOVERY_PORT)); } catch (Exception ignored) {}
            }
            long end = System.currentTimeMillis() + ms;
            byte[] buf = new byte[1024];
            while (true) {
                long left = end - System.currentTimeMillis();
                if (left <= 0) break;
                s.setSoTimeout((int) left);
                DatagramPacket p = new DatagramPacket(buf, buf.length);
                try { s.receive(p); } catch (SocketTimeoutException timeUp) { break; }
                try {
                    JSONObject info = new JSONObject(new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8));
                    String name = info.optString("name", "");
                    if (!"textshare".equals(info.optString("app")) || name.isEmpty()) continue;
                    String key = name.toLowerCase();
                    if (found.containsKey(key)) continue;
                    found.put(key, new JSONObject().put("name", name).put("address", p.getAddress().getHostAddress())
                            .put("port", info.optInt("port", PORT)));
                } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {
            // no network: nothing found
        }
        JSONArray out = new JSONArray();
        for (JSONObject o : found.values()) out.put(o);
        return out;
    }

    /** The broadcast address of each IPv4 network the phone is on (e.g. 192.168.1.255). */
    private static Set<InetAddress> subnetBroadcasts(Context c) {
        Set<InetAddress> out = new LinkedHashSet<>();
        try {
            ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
            Network n = cm.getActiveNetwork();                     // the Wi-Fi, when the phone is on it
            LinkProperties lp = n == null ? null : cm.getLinkProperties(n);
            if (lp != null) {
                for (LinkAddress la : lp.getLinkAddresses()) {
                    if (!(la.getAddress() instanceof Inet4Address)) continue;
                    byte[] a = la.getAddress().getAddress();
                    int ip = ((a[0] & 0xff) << 24) | ((a[1] & 0xff) << 16) | ((a[2] & 0xff) << 8) | (a[3] & 0xff);
                    int prefix = la.getPrefixLength();
                    if (prefix <= 0 || prefix >= 31) continue;
                    int b = ip | (-1 >>> prefix);
                    out.add(InetAddress.getByAddress(new byte[]{(byte) (b >>> 24), (byte) (b >>> 16), (byte) (b >>> 8), (byte) b}));
                }
            }
        } catch (Exception ignored) {}
        return out;
    }
}
