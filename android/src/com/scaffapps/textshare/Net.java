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
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/**
 * Talking to the computer that keeps the history (the Instant Share server on the same network), and what this phone
 * remembers about it. Every request says which device is asking (its id, made up once) and its name, as the
 * Windows app does, so the phone shows up in the others' "Who should see it" list.
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

    /** http://address:port of the computer that keeps the history, or null before setup. */
    static String base(Context c) {
        SharedPreferences p = prefs(c);
        String host = p.getString("address", null);
        if (host == null) host = p.getString("host", null);
        if (host == null) return null;
        return "http://" + (host.contains(":") ? "[" + host + "]" : host) + ":" + p.getInt("port", PORT);
    }

    static String hostName(Context c) { return prefs(c).getString("host", ""); }

    static HttpURLConnection open(Context c, String url, int readTimeoutMs) throws Exception {
        HttpURLConnection con = (HttpURLConnection) URI.create(url).toURL().openConnection();
        con.setConnectTimeout(8000);
        con.setReadTimeout(readTimeoutMs);
        con.setRequestProperty("X-TextShare", "1");
        if (c != null) {
            con.setRequestProperty("X-TextShare-Device", deviceId(c));
            con.setRequestProperty("X-TextShare-Name", URLEncoder.encode(name(c), "UTF-8").replace("+", "%20"));
            con.setRequestProperty("X-TextShare-Kind", "app");
        }
        return con;
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
            if (con.getResponseCode() != 200) throw new java.io.IOException("the computer answered " + con.getResponseCode());
            return bytes(con.getInputStream());
        } finally {
            con.disconnect();
        }
    }

    /** Instant Share's answer at host:port (it says its computer's name), or null. */
    static JSONObject hello(String host, int port, int timeoutMs) {
        HttpURLConnection con = null;
        try {
            con = open(null, "http://" + (host.contains(":") ? "[" + host + "]" : host) + ":" + port + "/api/hello", timeoutMs);
            con.setConnectTimeout(timeoutMs);
            if (con.getResponseCode() != 200) return null;
            JSONObject o = new JSONObject(new String(bytes(con.getInputStream()), StandardCharsets.UTF_8));
            return o.has("server") ? o : null;
        } catch (Exception e) {
            return null;
        } finally {
            if (con != null) con.disconnect();
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
