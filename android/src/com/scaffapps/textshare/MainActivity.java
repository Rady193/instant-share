package com.scaffapps.textshare;

import android.app.Activity;
import android.app.NotificationManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.OpenableColumns;
import android.provider.Settings;
import android.util.Base64;
import android.view.View;
import android.view.WindowInsetsController;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URLEncoder;

/**
 * Instant Share for Android: the Instant Share page of the computer that keeps the history, full screen, with what a
 * page cannot do on its own: hearing of new shares while closed (Listen), copying photos and saving files, sharing
 * from other apps (Android's Share menu), and the setup that finds that computer on the Wi-Fi.
 */
public class MainActivity extends Activity {
    static final String EXTRA_VIEW_URL = "view_url", EXTRA_VIEW_NAME = "view_name";
    private static final int FILE_REQUEST = 1, NOTIFY_REQUEST = 2, FOLDER_REQUEST = 3;
    private static final int FILE_MAX = 5 * 1024 * 1024;
    private int folderCall = -1;                   // the page's call waiting for the folder picker
    private boolean clearHistory;                  // after setup, Back never returns to it
    private static final String SETUP = "file:///android_asset/setup.html";
    /** On screen right now: the page shows new items itself, so no notifications for them. */
    static volatile boolean foreground;

    private WebView web;
    private ValueCallback<Uri[]> fileCallback;
    private String shared;                         // something shared from another app, waiting for the page

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);                          // the page keeps its draft and choices in localStorage
        s.setAllowFileAccess(true);                            // the setup page is one of the app's own files
        s.setSupportMultipleWindows(false);                    // links that open a new window load here instead
        s.setUserAgentString(s.getUserAgentString() + " TextShareAndroid/" + BuildInfo.VERSION);
        web.addJavascriptInterface(new Bridge(), "TextShareAndroid");
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), FILE_REQUEST);
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        applyTheme(theme());
        take(getIntent());
        if (Net.base(this) == null) setup(false);
        else { page(); Listen.start(this); }
    }

    // ---------------------------------------------------------------- what is on screen

    private String theme() { return "light".equals(Net.prefs(this).getString("theme", "dark")) ? "light" : "dark"; }

    private void page() {
        clearHistory = true;
        try {
            web.loadUrl(Net.base(this) + "/?app=1&id=" + Net.deviceId(this) + "&me="
                    + URLEncoder.encode(Net.name(this), "UTF-8").replace("+", "%20") + "&theme=" + theme());
        } catch (java.io.UnsupportedEncodingException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    /** The app's own setup page: find the computer that keeps the history (or say it can't be reached). */
    private void setup(boolean unreachable) {
        web.loadUrl(SETUP + "?theme=" + theme() + (unreachable ? "&unreachable=1" : ""));
    }

    /** The status bar and the navigation bar in the page's appearance (Multi Screens' colours). */
    @SuppressWarnings("deprecation")
    private void applyTheme(String theme) {
        boolean light = "light".equals(theme);
        int bar = light ? Color.WHITE : 0xFF191A1B;
        getWindow().setStatusBarColor(bar);
        getWindow().setNavigationBarColor(bar);
        web.setBackgroundColor(light ? 0xFFEEF0F4 : 0xFF121314);
        if (Build.VERSION.SDK_INT >= 30) {
            int both = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) c.setSystemBarsAppearance(light ? both : 0, both);
        } else {
            View d = getWindow().getDecorView();
            int flags = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            d.setSystemUiVisibility(light ? d.getSystemUiVisibility() | flags : d.getSystemUiVisibility() & ~flags);
        }
    }

    /** Answers a call the page is waiting for (textShareDone in the page). */
    private void answer(int call, JSONObject result) {
        runOnUiThread(() -> web.evaluateJavascript("window.textShareDone && window.textShareDone(" + call + "," + result + ")", null));
    }

    private static JSONObject ok() { try { return new JSONObject().put("ok", true); } catch (Exception e) { return new JSONObject(); } }

    private static JSONObject error(String text) {
        try { return new JSONObject().put("error", text); } catch (Exception e) { return new JSONObject(); }
    }

    // ---------------------------------------------------------------- asking once: notifications, battery

    private void askOnce() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED
                && !Net.prefs(this).getBoolean("asked_notify", false)) {
            Net.prefs(this).edit().putBoolean("asked_notify", true).apply();
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, NOTIFY_REQUEST);
            return;
        }
        askBattery();
    }

    /** Spared from battery optimisation, Android does not cut the open connection, so shares arrive at once. */
    private void askBattery() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm.isIgnoringBatteryOptimizations(getPackageName()) || Net.prefs(this).getBoolean("asked_battery", false)) return;
        Net.prefs(this).edit().putBoolean("asked_battery", true).apply();
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (ActivityNotFoundException ignored) {}
    }

    @Override
    public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        if (request == NOTIFY_REQUEST) askBattery();
    }

    // ---------------------------------------------------------------- shared from other apps; opened from a notification

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        take(intent);
    }

    private void take(Intent intent) {
        if (intent == null) return;
        String viewUrl = intent.getStringExtra(EXTRA_VIEW_URL);
        if (viewUrl != null) {                                 // "Open" on a notification for a PDF
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).cancel(intent.getIntExtra(Actions.EXTRA_NOTIFICATION, 0));
            view(viewUrl, intent.getStringExtra(EXTRA_VIEW_NAME));
            intent.removeExtra(EXTRA_VIEW_URL);
            return;
        }
        if (!Intent.ACTION_SEND.equals(intent.getAction())) return;
        try {
            JSONObject s = new JSONObject();
            String text = intent.getStringExtra(Intent.EXTRA_TEXT);
            if (text != null && !text.trim().isEmpty()) s.put("text", text);
            Uri stream = sharedStream(intent);
            if (stream != null) s.put("file", readShared(stream, intent.getType()));
            shared = s.toString();
        } catch (Exception e) {
            Toast.makeText(this, "Instant Share could not read what was shared.", Toast.LENGTH_LONG).show();
        }
        intent.setAction(Intent.ACTION_MAIN);                  // not taken twice (after turning the phone, say)
        String url = web.getUrl(), base = Net.base(this);
        if (shared != null && url != null && base != null && url.startsWith(base)) {
            String s = shared;
            shared = null;
            web.evaluateJavascript("window.textShareIncoming && window.textShareIncoming(" + s + ")", null);
        }
    }

    /** The shared file's address. The typed way exists from Android 13; older phones take the older one. */
    @SuppressWarnings("deprecation")
    private static Uri sharedStream(Intent intent) {
        return Build.VERSION.SDK_INT >= 33 ? intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class)
                : (Uri) intent.getParcelableExtra(Intent.EXTRA_STREAM);
    }

    private JSONObject readShared(Uri uri, String type) throws Exception {
        String name = "shared file";
        long size = -1;
        try (Cursor q = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null)) {
            if (q != null && q.moveToFirst()) {
                if (!q.isNull(0)) name = q.getString(0);
                if (!q.isNull(1)) size = q.getLong(1);
            }
        }
        String t = getContentResolver().getType(uri);
        if (t == null) t = type;
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (InputStream in = getContentResolver().openInputStream(uri)) {
            byte[] buf = new byte[16384];
            for (int n; in != null && (n = in.read(buf)) > 0 && out.size() <= FILE_MAX; ) out.write(buf, 0, n);
        }
        if (size < 0 || out.size() > FILE_MAX) size = Math.max(size, out.size());
        boolean fits = out.size() <= FILE_MAX;                 // bigger: the page says it is too big
        return new JSONObject().put("name", name).put("type", t == null ? "" : t).put("size", size)
                .put("data", fits ? Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP) : "");
    }

    /** A photo or PDF from the history, in the phone's own viewer. */
    private void view(String url, String name) {
        new Thread(() -> {
            try {
                Files.view(this, name == null ? Uri.parse(url).getLastPathSegment() : name, Net.get(this, url));
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "Could not open it: " + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }, "textshare-view").start();
    }

    private JSONObject options() {
        try {
            return new JSONObject().put("android", true).put("save_folder", Files.folderLabel(this))
                    .put("save_chosen", Files.chosenFolder(this) != null);
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request == FOLDER_REQUEST) {
            Uri tree = result == RESULT_OK && data != null ? data.getData() : null;
            if (tree != null) {
                try {
                    getContentResolver().takePersistableUriPermission(tree,
                            Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                    Net.prefs(this).edit().putString("save_tree", tree.toString()).apply();
                } catch (SecurityException notKept) {
                    Toast.makeText(this, "Android did not allow saving in that folder.", Toast.LENGTH_LONG).show();
                }
            }
            if (folderCall >= 0) { answer(folderCall, options()); folderCall = -1; }
            return;
        }
        if (request == FILE_REQUEST && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            fileCallback = null;
        }
    }

    @Override
    protected void onResume() { super.onResume(); foreground = true; }

    @Override
    protected void onPause() { foreground = false; super.onPause(); }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        // first whatever is open in the page (settings, a menu, a photo...), then away: never back to the setup
        web.evaluateJavascript("window.textShareBack ? window.textShareBack() : false", handled -> {
            if (!"true".equals(handled)) moveTaskToBack(true);   // stays ready, like closing the window on Windows
        });
    }

    // ---------------------------------------------------------------- the page's links

    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            Uri target = req.getUrl();
            if ("file".equals(target.getScheme())) return false;
            String base = Net.base(MainActivity.this);
            String url = target.toString();
            if (base != null && url.startsWith(base + "/")) {
                String path = target.getPath() == null ? "" : target.getPath();
                if (path.startsWith("/files/")) { view(url, target.getLastPathSegment()); return true; }
                if (!path.startsWith("/download")) return false;   // Instant Share's own page
            }
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, target).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            } catch (ActivityNotFoundException ignored) {}
            return true;
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            if (clearHistory && url != null && !url.startsWith("file:")) { clearHistory = false; view.clearHistory(); }
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
            if (req.isForMainFrame() && !"file".equals(req.getUrl().getScheme())) setup(true);
        }
    }

    // ---------------------------------------------------------------- what the pages may ask (window.TextShareAndroid)

    private class Bridge {
        @JavascriptInterface
        public String info() {
            try {
                return new JSONObject().put("me", Net.name(MainActivity.this)).put("host", Net.hostName(MainActivity.this))
                        .put("version", BuildInfo.VERSION).put("theme", theme()).toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public void find(int call) {
            new Thread(() -> {
                try {
                    JSONArray found = Net.discover(MainActivity.this, 1800);
                    answer(call, new JSONObject().put("found", found).put("host", Net.hostName(MainActivity.this)));
                } catch (Exception e) {
                    answer(call, error(String.valueOf(e.getMessage())));
                }
            }, "textshare-find").start();
        }

        /** A computer typed in: "OFFICE-PC", "192.168.1.20", or either with ":port". */
        @JavascriptInterface
        public void connect(int call, String typed) {
            new Thread(() -> {
                String text = typed.trim().replaceFirst("^https?://", "").replaceAll("/+$", "");
                String host = text;
                int port = Net.PORT;
                int colon = text.lastIndexOf(':');
                if (colon > 0 && text.indexOf(':') == colon) {
                    host = text.substring(0, colon);
                    try { port = Integer.parseInt(text.substring(colon + 1)); }
                    catch (NumberFormatException e) { answer(call, error("The part after the colon must be a port number, for example OFFICE-PC:8765.")); return; }
                }
                host = host.replace("[", "").replace("]", "");
                if (host.isEmpty()) { answer(call, error("Type the name or the address of the computer that keeps the history.")); return; }
                join(call, null, host, port);
            }, "textshare-connect").start();
        }

        @JavascriptInterface
        public void join(int call, String name, String address, int port) {
            new Thread(() -> {
                JSONObject h = Net.hello(address, port, 5000);
                if (h == null) {
                    answer(call, error("Instant Share does not answer at " + address + " (port " + port + "). Check that it is running "
                            + "there and that this phone is on the same Wi-Fi."));
                    return;
                }
                Net.prefs(MainActivity.this).edit().putString("host", h.optString("server", name == null ? address : name))
                        .putString("address", address).putInt("port", port).apply();
                answer(call, ok());
                runOnUiThread(() -> { page(); Listen.restart(MainActivity.this); askOnce(); });
            }, "textshare-join").start();
        }

        @JavascriptInterface
        public void retry() { runOnUiThread(MainActivity.this::page); }

        @JavascriptInterface
        public void back() { if (Net.base(MainActivity.this) != null) runOnUiThread(MainActivity.this::page); }

        @JavascriptInterface
        public void openSetup() { runOnUiThread(() -> setup(false)); }

        @JavascriptInterface
        public String takeShared() {
            String s = shared;
            shared = null;
            return s;
        }

        @JavascriptInterface
        public void setTheme(String theme) {
            String t = "light".equals(theme) ? "light" : "dark";
            Net.prefs(MainActivity.this).edit().putString("theme", t).apply();
            runOnUiThread(() -> applyTheme(t));
        }

        @JavascriptInterface
        public void setName(String name) {
            String n = name == null ? "" : name.trim();
            if (!n.isEmpty() && n.length() <= 60) Net.prefs(MainActivity.this).edit().putString("name", n).apply();
        }

        @JavascriptInterface
        public void openFile(String url, String name) { view(url, name); }

        @JavascriptInterface
        public String getOptions() { return options().toString(); }

        @JavascriptInterface
        public void chooseSaveFolder(int call) {
            runOnUiThread(() -> {
                folderCall = call;
                try {
                    startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE), FOLDER_REQUEST);
                } catch (ActivityNotFoundException e) {
                    folderCall = -1;
                    answer(call, options());
                }
            });
        }

        @JavascriptInterface
        public String resetSaveFolder() {
            Net.prefs(MainActivity.this).edit().remove("save_tree").apply();
            return options().toString();
        }

        @JavascriptInterface
        public void copyImage(int call, String url) {
            new Thread(() -> {
                try {
                    Files.copyImage(MainActivity.this, Uri.parse(url).getLastPathSegment(), Net.get(MainActivity.this, url));
                    answer(call, ok());
                } catch (Exception e) {
                    answer(call, error("Could not copy the photo: " + e.getMessage()));
                }
            }, "textshare-copy").start();
        }

        @JavascriptInterface
        public void saveFile(int call, String url, String name) {
            new Thread(() -> {
                try {
                    String saved = Files.save(MainActivity.this, name, Net.get(MainActivity.this, url));
                    answer(call, ok().put("name", saved));
                } catch (Exception e) {
                    answer(call, error("Could not save it: " + e.getMessage()));
                }
            }, "textshare-save").start();
        }
    }
}
