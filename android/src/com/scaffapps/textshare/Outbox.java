package com.scaffapps.textshare;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Pattern;

/**
 * What this phone wrote while it could not reach the computer that keeps the history ("Send later"): one file each in
 * the app's own storage, shared oldest first as soon as that computer answers (Listen tries before each wait, and the
 * page asks when it sees the computer again), also while the app is closed. Each carries the id it was given when it
 * was written, so the history never keeps one twice, even when an answer is lost on the way and it is sent again. One
 * the history refuses (too big, not allowed, history full) stays, with why, until it is tried again or deleted.
 */
final class Outbox {
    static final String CHANNEL_SENT = "sent_later";
    private static final int SENT_NOTICE = -2;               // (items use their own ids, the connection notice 8765)
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9_-]{8,64}");
    private static final long FILE_MAX = 5 * 1024 * 1024, TEXT_MAX = 20 * 1024 * 1024;
    private static final Object LOCK = new Object();
    private static final AtomicBoolean SENDING = new AtomicBoolean();

    /** The open page, told when the list changes (sent: how many were shared just now). */
    interface Watcher { void changed(int sent); }
    static volatile Watcher watcher;

    private Outbox() {}

    // ---------------------------------------------------------------- kept on the phone: outbox/<written>-<uid>.json

    private static File folder(Context c) {
        File f = new File(c.getFilesDir(), "outbox");
        if (!f.isDirectory()) f.mkdirs();                    // (if it fails, keeping one says so)
        return f;
    }

    private static File[] files(Context c) {
        File[] f = folder(c).listFiles((dir, name) -> name.endsWith(".json"));
        if (f == null) return new File[0];
        Arrays.sort(f);                                       // by when they were written
        return f;
    }

    private static File find(Context c, String uid) {
        for (File f : files(c)) {
            String n = f.getName();
            int dash = n.indexOf('-');
            if (dash > 0 && n.substring(dash + 1, n.length() - 5).equals(uid)) return f;
        }
        return null;
    }

    private static JSONObject read(File f) {
        try {
            JSONObject o = new JSONObject(new String(Net.bytes(new FileInputStream(f)), StandardCharsets.UTF_8));
            return ID.matcher(o.optString("uid")).matches() ? o : null;
        } catch (Exception e) {
            return null;
        }
    }

    private static void write(Context c, JSONObject e) throws Exception {
        File dir = folder(c);
        String name = String.format(Locale.ROOT, "%013d-%s.json", e.getLong("written"), e.getString("uid"));
        File tmp = new File(dir, name + ".tmp"), out = new File(dir, name);
        try (OutputStream o = new FileOutputStream(tmp)) { o.write(e.toString().getBytes(StandardCharsets.UTF_8)); }
        if (!tmp.renameTo(out)) throw new IOException("could not keep it");
    }

    private static String text(JSONObject e, String name) {
        Object v = e.opt(name);
        return v instanceof String ? (String) v : "";
    }

    static List<JSONObject> entries(Context c) {
        synchronized (LOCK) {
            List<JSONObject> out = new ArrayList<>();
            for (File f : files(c)) {
                JSONObject o = read(f);
                if (o != null) out.add(o);
            }
            return out;
        }
    }

    /** Anything that waits to be sent (not refused already)? */
    static boolean sendable(Context c) {
        for (JSONObject e : entries(c)) if (text(e, "error").isEmpty()) return true;
        return false;
    }

    /** For the pages: oldest first, without the files' contents, the start of the text only. */
    static JSONArray list(Context c) {
        JSONArray out = new JSONArray();
        for (JSONObject e : entries(c)) {
            try {
                String t = text(e, "text");
                JSONObject s = new JSONObject().put("uid", e.getString("uid"))
                        .put("text", t.length() > 4000 ? t.substring(0, 4000) : t).put("size", t.length())
                        .put("lines", t.isEmpty() ? 0 : t.split("\n", -1).length)
                        .put("to", e.optJSONArray("to") == null ? new JSONArray() : e.getJSONArray("to"))
                        .put("to_names", e.optJSONArray("to_names") == null ? new JSONArray() : e.getJSONArray("to_names"))
                        .put("written", e.getLong("written")).put("error", text(e, "error"));
                JSONObject f = e.optJSONObject("file");
                s.put("file", f == null ? JSONObject.NULL : new JSONObject().put("name", text(f, "name"))
                        .put("type", text(f, "type")).put("size", f.optLong("size")));
                out.put(s);
            } catch (Exception ignored) {
                // a damaged one is left out
            }
        }
        return out;
    }

    /** A share to keep, as the page made it: {uid, text, to, to_names, file: {name, type, size, data}, written}. */
    static JSONObject add(Context c, String json) {
        try {
            JSONObject in = new JSONObject(json);
            String uid = text(in, "uid");
            if (!ID.matcher(uid).matches()) return error("That was not a proper share.");
            String t = text(in, "text");
            if (t.getBytes(StandardCharsets.UTF_8).length > TEXT_MAX) return error("That is too long to share (more than 20 MB).");
            JSONObject file = in.optJSONObject("file"), kept = null;
            if (file != null) {
                String data = text(file, "data"), name = text(file, "name");
                if (data.isEmpty() || name.isEmpty()) return error("The file could not be read.");
                long size = (long) data.length() * 3 / 4 - (data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0);
                if (size > FILE_MAX) {
                    return error(String.format(Locale.ROOT, "That file is %.2f MB. Files can be up to 5 MB.", size / 1048576.0));
                }
                kept = new JSONObject().put("name", name.length() > 255 ? name.substring(0, 255) : name)
                        .put("type", text(file, "type")).put("size", size).put("data", data);
            }
            if (t.trim().isEmpty() && kept == null) return error("There is nothing to share.");
            JSONArray to = new JSONArray(), names = new JSONArray(), inTo = in.optJSONArray("to"), inNames = in.optJSONArray("to_names");
            for (int i = 0; inTo != null && i < inTo.length() && to.length() < 50; i++) {
                String d = inTo.optString(i, "");
                if (ID.matcher(d).matches()) to.put(d);
            }
            for (int i = 0; to.length() > 0 && inNames != null && i < inNames.length() && i < 50; i++) names.put(inNames.optString(i, ""));
            long now = System.currentTimeMillis(), written = in.optLong("written", now);
            if (written <= 0 || written > now) written = now;
            JSONObject e = new JSONObject().put("uid", uid).put("text", t).put("to", to).put("to_names", names).put("written", written);
            if (kept != null) e.put("file", kept);
            synchronized (LOCK) { write(c, e); }
            return ok();
        } catch (Exception e) {
            return error("Could not keep it on this phone: " + e.getMessage());
        }
    }

    /** The whole of one, with its file: back into the editor. */
    static JSONObject get(Context c, String uid) {
        synchronized (LOCK) {
            File f = find(c, uid);
            return f == null ? null : read(f);
        }
    }

    static JSONObject remove(Context c, String uid) {
        synchronized (LOCK) {
            File f = find(c, uid);
            if (f != null && !f.delete()) return error("Could not delete it.");
        }
        return ok();
    }

    /** Refused by the history (why), or to be tried again (""). */
    static void setError(Context c, String uid, String why) {
        synchronized (LOCK) {
            File f = find(c, uid);
            JSONObject e = f == null ? null : read(f);
            if (e == null) return;
            try {
                if (why.isEmpty()) e.remove("error");
                else e.put("error", why.length() > 300 ? why.substring(0, 300) : why);
                write(c, e);
            } catch (Exception ignored) {
                // left as it was
            }
        }
    }

    // ---------------------------------------------------------------- sending

    /** Try now, away from the caller's thread (the page kept one, or saw the computer again). */
    static void sendSoon(Context c) {
        Context app = c.getApplicationContext();
        new Thread(() -> sendAll(app), "textshare-outbox").start();
    }

    /** Shares what waits, oldest first, and stops at the first sign the computer is out of reach. How many went. */
    static int sendAll(Context c) {
        if (!Net.paired(c) || !SENDING.compareAndSet(false, true)) return 0;
        int sent = 0, changed = 0;
        try {
            String base = Net.base(c);
            for (JSONObject e : entries(c)) {
                if (!text(e, "error").isEmpty()) continue;
                String uid = e.getString("uid");
                JSONObject body = new JSONObject().put("text", text(e, "text")).put("device", Net.name(c))
                        .put("to", e.optJSONArray("to") == null ? new JSONArray() : e.getJSONArray("to")).put("uid", uid)
                        .put("waited", Math.max(0, System.currentTimeMillis() - e.getLong("written")));
                JSONObject f = e.optJSONObject("file");
                if (f != null) body.put("file", new JSONObject().put("name", text(f, "name")).put("data", text(f, "data")));
                int code;
                String answer;
                HttpURLConnection con = null;
                try {
                    con = Net.open(c, base + "/api/items", 60000);
                    con.setRequestMethod("POST");
                    con.setDoOutput(true);
                    con.setRequestProperty("Content-Type", "application/json");
                    byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                    con.setFixedLengthStreamingMode(bytes.length);
                    try (OutputStream o = con.getOutputStream()) { o.write(bytes); }
                    code = con.getResponseCode();
                    InputStream in = code >= 400 ? con.getErrorStream() : con.getInputStream();
                    answer = in == null ? "" : new String(Net.bytes(in), StandardCharsets.UTF_8);
                } catch (Exception away) {
                    break;                                     // out of reach again: later
                } finally {
                    if (con != null) con.disconnect();
                }
                if (code == 200 || code == 201) {
                    remove(c, uid);
                    sent++;
                    changed++;
                    continue;
                }
                if (code == 401 || (code >= 500 && code != 507)) break;     // not joined any more (Listen says so), or trouble there
                String why = "";
                try { why = new JSONObject(answer).optString("error", ""); } catch (Exception ignored) {}
                setError(c, uid, why.isEmpty() ? "the computer answered " + code : why);
                changed++;
            }
        } catch (Exception ignored) {
            // tried again later
        } finally {
            SENDING.set(false);
        }
        if (changed > 0) {
            Watcher w = watcher;
            if (w != null) w.changed(sent);
        }
        if (sent > 0 && !MainActivity.foreground) notifySent(c, sent);
        return sent;
    }

    /** What was written while away is shared now: said in a quiet notification (the page says it when it is open). */
    private static void notifySent(Context c, int n) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (!nm.areNotificationsEnabled()) return;
        if (nm.getNotificationChannel(CHANNEL_SENT) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL_SENT, "Sent later", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("When what you wrote while away is shared");
            nm.createNotificationChannel(ch);
        }
        String what = (n == 1 ? "The message you wrote while away is" : "The " + n + " messages you wrote while away are")
                + " in the history on " + Net.hostName(c) + " now.";
        PendingIntent open = PendingIntent.getActivity(c, 7, new Intent(c, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE);
        Notification note = new Notification.Builder(c, CHANNEL_SENT)
                .setSmallIcon(R.drawable.ic_stat_share).setColor(0xFF5B2C83)
                .setContentTitle("Shared what you wrote while away").setContentText(what)
                .setStyle(new Notification.BigTextStyle().bigText(what))
                .setContentIntent(open).setAutoCancel(true).build();
        try { nm.notify(SENT_NOTICE, note); } catch (SecurityException permissionWithdrawn) { /* nothing to do */ }
    }

    /** The other devices, kept for the can't-reach page's "To" (who should see what is written there). */
    static void rememberDevices(Context c) {
        try {
            JSONArray all = new JSONObject(new String(Net.get(c, "/api/devices"), StandardCharsets.UTF_8)).getJSONArray("devices");
            JSONArray out = new JSONArray();
            for (int i = 0; i < all.length(); i++) {
                JSONObject d = all.getJSONObject(i);
                if (!d.optBoolean("me")) out.put(new JSONObject().put("id", d.getString("id")).put("name", d.getString("name")));
            }
            Net.prefs(c).edit().putString("devices", out.toString()).apply();
        } catch (Exception ignored) {
            // the list as it was
        }
    }

    static JSONObject ok() {
        try { return new JSONObject().put("ok", true); } catch (Exception e) { return new JSONObject(); }
    }

    static JSONObject error(String text) {
        try { return new JSONObject().put("error", text); } catch (Exception e) { return new JSONObject(); }
    }
}
