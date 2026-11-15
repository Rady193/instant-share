package com.scaffapps.textshare;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * New versions: Instant Share is published as GitHub releases. Once a day (when allowed in Settings), and when asked,
 * the phone asks GitHub for the newest one; when it is newer, a notification says so. Updating downloads the app,
 * checks it against the SHA-256 published with the release, and hands it to Android's installer (the same signing
 * key, so it installs over this one and keeps the setup).
 */
final class Update {
    static final String REPO = "Rady193/instant-share";
    static final String EXTRA_UPDATE = "update";
    private static final long EVERY = 24L * 3600 * 1000;
    private static final String CHANNEL = "updates";
    private static final Pattern HASH = Pattern.compile("\\|\\s*`([^`|]+)`\\s*\\|[^|\\n]*\\|\\s*`([0-9A-Fa-f]{64})`");

    private Update() {}

    static boolean enabled(Context c) { return Net.prefs(c).getBoolean("update_check", true); }

    static void setEnabled(Context c, boolean on) { Net.prefs(c).edit().putBoolean("update_check", on).apply(); }

    /** What the last check found (for Settings): {current, latest, available, can_install, page, checked} or an error. */
    static JSONObject state(Context c) {
        try {
            JSONObject o = new JSONObject(Net.prefs(c).getString("update_state", "{}"));
            o.put("current", BuildInfo.VERSION);
            if (o.has("latest")) o.put("available", newer(o.getString("latest"), BuildInfo.VERSION));
            return o;
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    static boolean newer(String a, String b) {
        int[] x = parts(a), y = parts(b);
        for (int i = 0; i < 3; i++) if (x[i] != y[i]) return x[i] > y[i];
        return false;
    }

    private static int[] parts(String v) {
        int[] out = new int[3];
        Matcher m = Pattern.compile("\\d+").matcher(v == null ? "" : v);
        for (int i = 0; i < 3 && m.find(); i++) out[i] = Integer.parseInt(m.group());
        return out;
    }

    /** Asks GitHub now. With tell, a newer version is announced once in a notification. */
    static JSONObject check(Context c, boolean tell) {
        JSONObject st = new JSONObject();
        try {
            HttpURLConnection con = (HttpURLConnection) new URL("https://api.github.com/repos/" + REPO + "/releases/latest").openConnection();
            con.setConnectTimeout(10000);
            con.setReadTimeout(15000);
            con.setRequestProperty("Accept", "application/vnd.github+json");
            con.setRequestProperty("User-Agent", "InstantShare-update-check");
            JSONObject rel;
            try {
                if (con.getResponseCode() != 200) throw new IOException("GitHub answered " + con.getResponseCode());
                rel = new JSONObject(new String(Net.bytes(con.getInputStream()), StandardCharsets.UTF_8));
            } finally {
                con.disconnect();
            }
            String version = rel.getString("tag_name").replaceFirst("^v", "");
            String apk = "Instant-Share-" + version + ".apk", url = null, sha = null;
            JSONArray assets = rel.optJSONArray("assets");
            for (int i = 0; assets != null && i < assets.length(); i++) {
                JSONObject a = assets.getJSONObject(i);
                if (apk.equals(a.optString("name"))) url = a.optString("browser_download_url");
            }
            Matcher m = HASH.matcher(rel.optString("body"));
            while (m.find()) if (apk.equals(m.group(1).trim())) sha = m.group(2).toLowerCase();
            boolean available = newer(version, BuildInfo.VERSION);
            st.put("latest", version).put("page", rel.optString("html_url")).put("checked", System.currentTimeMillis())
                    .put("can_install", url != null && sha != null).put("apk_url", url == null ? "" : url)
                    .put("apk_sha", sha == null ? "" : sha);
            Net.prefs(c).edit().putString("update_state", st.toString()).putLong("update_checked", System.currentTimeMillis()).apply();
            if (available && tell && !version.equals(Net.prefs(c).getString("update_told", ""))) {
                Net.prefs(c).edit().putString("update_told", version).apply();
                announce(c, version);
            }
            return state(c);
        } catch (Exception e) {
            try {
                return state(c).put("error", "Could not ask GitHub (" + e.getMessage() + "). Is the phone on the internet?");
            } catch (Exception ignored) {
                return new JSONObject();
            }
        }
    }

    /** The daily check, when allowed and due (in the background). */
    static void daily(Context c) {
        if (!enabled(c) || System.currentTimeMillis() - Net.prefs(c).getLong("update_checked", 0) < EVERY) return;
        Context app = c.getApplicationContext();
        new Thread(() -> check(app, true), "textshare-update-check").start();
    }

    /** Download, check, and open Android's installer for the new version. {ok} or {error}. */
    static JSONObject install(Activity a) {
        try {
            JSONObject st = state(a);
            if (!st.optBoolean("available")) st = check(a, false);
            if (!st.optBoolean("available")) return new JSONObject().put("error", BuildInfo.VERSION + " is the newest version already.");
            if (!st.optBoolean("can_install"))
                return new JSONObject().put("error", "That release has no Android app with a checksum. Get it from the Releases page.").put("page", st.optString("page"));
            if (!a.getPackageManager().canRequestPackageInstalls()) {
                a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + a.getPackageName())));
                return new JSONObject().put("error", "Allow Instant Share to install apps (the switch that just opened), then come back and press Update again.");
            }
            HttpURLConnection con = (HttpURLConnection) new URL(st.getString("apk_url")).openConnection();
            con.setConnectTimeout(10000);
            con.setReadTimeout(60000);
            con.setRequestProperty("User-Agent", "InstantShare-update");
            byte[] data;
            try {
                if (con.getResponseCode() != 200) throw new IOException("GitHub answered " + con.getResponseCode());
                data = Net.bytes(con.getInputStream());
            } finally {
                con.disconnect();
            }
            if (!Secure.hex(Secure.sha256(data)).equals(st.getString("apk_sha")))
                return new JSONObject().put("error", "The download did not match its published checksum, so it was not installed. Try again later.");
            Uri apk = Files.cached(a, "Instant-Share-" + st.getString("latest") + ".apk", data);
            a.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(apk, "application/vnd.android.package-archive")
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK));
            return new JSONObject().put("ok", true);
        } catch (Exception e) {
            try { return new JSONObject().put("error", "Could not update: " + e.getMessage()); } catch (Exception ignored) { return new JSONObject(); }
        }
    }

    private static void announce(Context c, String version) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (!nm.areNotificationsEnabled()) return;
        if (nm.getNotificationChannel(CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "New versions", NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("When a new version of Instant Share is out");
            nm.createNotificationChannel(ch);
        }
        PendingIntent open = PendingIntent.getActivity(c, 7, new Intent(c, MainActivity.class).putExtra(EXTRA_UPDATE, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(c, CHANNEL).setSmallIcon(R.drawable.ic_stat_share).setColor(0xFF297AA0)
                .setContentTitle("Instant Share " + version + " is available")
                .setContentText("This phone has " + BuildInfo.VERSION + ". Tap to update; the setup stays.")
                .setContentIntent(open).setAutoCancel(true).build();
        try { nm.notify(7, n); } catch (SecurityException ignored) {}
    }
}
