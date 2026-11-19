#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Adds "Modbus TCP" and "Max. charging current" to the Enovates admin page.

Runs ON the wallbox (Python 2.7, standard library only). Takes the original
LCCL jar (backup *.orig), modifies com/enovates/lccl/admin/admin.html and writes
the patched jar. No Enovates files are distributed with this project.

  python patch_webui.py <original.jar> <target.jar>      patch
  python patch_webui.py <original.jar> --dry-run <out>   only write the patched admin.html
"""
import sys
import zipfile

ENTRY = "com/enovates/lccl/admin/admin.html"
MARKER = "<!-- modbus-bridge patch -->"

# 1) Form fields: after the "Network config" block
ANCHOR_HTML = '</input><a href="network.html" id="network" class="btnStyle">change &gt;&gt;</a>\n\t\t\t\t</div>\n'
BLOCK_HTML = (
    '\t\t\t\t<!-- modbus-bridge patch -->\n'
    '\t\t\t\t<div>\n'
    '\t\t\t\t\t<label class="formLabel" for="modbustcp" id="modbustcp_label">Modbus TCP (port 502)</label> '
    '<select class="formInput config" id="modbustcp" name="modbustcp">\n'
    '\t\t\t\t\t\t<option value="none">Off - Enovates control</option>\n'
    '\t\t\t\t\t\t<option value="PhoenixRest">On - external control (evcc)</option>\n'
    '\t\t\t\t\t</select>\n'
    '\t\t\t\t</div>\n'
    '\t\t\t\t<div>\n'
    '\t\t\t\t\t<label class="formLabel" for="maxcurrent" id="maxcurrent_label">Max. charging current per phase</label> '
    '<select class="formInput config" id="maxcurrent" name="maxcurrent">\n'
    + "".join('\t\t\t\t\t\t<option value="%d">%d A</option>\n' % (a, a) for a in (6, 10, 13, 16, 20, 25, 32)) +
    '\t\t\t\t\t</select>\n'
    '\t\t\t\t</div>\n'
)

# 2) Load values: at the end of loadConfig()
ANCHOR_LOAD = 'setTimeout(function(){  getFieldCurrentValue("config.preset", function(v){$(\'#preset\').val(v)}); }, 1000);\n'
BLOCK_LOAD = r'''        // modbus-bridge patch
        getFieldCurrentValue("smart.external.limit.device", function(v){$('#modbustcp').val(v == "PhoenixRest" ? "PhoenixRest" : "none")});
        getFieldCurrentValue("chargepoint.max.amp.per.phase.soft.connector.1", function(v){
            var a = String(Math.round(parseFloat(v)));
            if ($('#maxcurrent option[value="' + a + '"]').length == 0) $('#maxcurrent').append('<option value="' + a + '">' + a + ' A</option>');
            $('#maxcurrent').val(a);
        });
'''

# 3) Save: in the save button handler, after the OCPP fields
ANCHOR_SAVE = 'saveValueToConfig("ocpp", $(\'#OCPPType\').val());\n'
BLOCK_SAVE = r'''
        // modbus-bridge patch: these settings only take effect after a restart
        var pending = [];
        var modbusNew = $('#modbustcp').val();
        var modbusOld = (config["smart.external.limit.device"] == "PhoenixRest") ? "PhoenixRest" : "none";
        if (modbusNew != modbusOld) {
            pending.push(["smart.external.limit.device", modbusNew]);
        }
        var maxNew = $('#maxcurrent').val();
        var maxOld = String(Math.round(parseFloat(config["chargepoint.max.amp.per.phase.soft.connector.1"])));
        if (maxNew && maxNew != maxOld) {
            pending.push(["chargepoint.max.amp.per.phase.soft", maxNew]);
            pending.push(["chargepoint.max.amp.per.phase.soft.connector.1", maxNew]);
        }
        var saveNext = function (i) {
            if (i >= pending.length) {
                if (confirm("Saved: Modbus TCP " + (modbusNew == "PhoenixRest" ? "ON" : "OFF") + ", max. " + maxNew + " A per phase.\nThe charger must restart to apply this. Restart now?")) {
                    rebootLCCL();
                }
                return;
            }
            $.ajax({
                type: "GET",
                url: basePathHandler + "configure?key=" + encodeURIComponent(pending[i][0]) + "&value=" + encodeURIComponent(pending[i][1]),
                dataType: 'json',
                success: function () { config[pending[i][0]] = pending[i][1]; saveNext(i + 1); },
                error: function () { alert("Could not save " + pending[i][0] + "."); }
            });
        };
        if (pending.length > 0) saveNext(0);
'''


def insert_after(text, anchor, block, what):
    if text.count(anchor) != 1:
        raise SystemExit("ERROR: anchor for '%s' found %d times (expected: 1). "
                         "Different firmware version?" % (what, text.count(anchor)))
    return text.replace(anchor, anchor + block)


def patch_html(html):
    if MARKER in html:
        raise SystemExit("ERROR: admin.html is already patched. Use the original jar (*.orig).")
    html = insert_after(html, ANCHOR_HTML, BLOCK_HTML, "form fields")
    html = insert_after(html, ANCHOR_LOAD, BLOCK_LOAD, "loadConfig")
    html = insert_after(html, ANCHOR_SAVE, BLOCK_SAVE, "save")
    return html


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    zin = zipfile.ZipFile(src)
    html = patch_html(zin.read(ENTRY).decode("utf-8"))

    if sys.argv[2] == "--dry-run":
        open(sys.argv[3], "wb").write(html.encode("utf-8"))
        print("patched admin.html written: %s" % sys.argv[3])
        return

    dst = sys.argv[2]
    zout = zipfile.ZipFile(dst, "w")
    for info in zin.infolist():
        data = zin.read(info.filename)
        if info.filename == ENTRY:
            data = html.encode("utf-8")
        zi = zipfile.ZipInfo(info.filename, info.date_time)
        zi.compress_type = info.compress_type
        zi.external_attr = info.external_attr
        zout.writestr(zi, data)
    zout.close()

    check = zipfile.ZipFile(dst)
    bad = check.testzip()
    if bad or MARKER not in check.read(ENTRY).decode("utf-8"):
        raise SystemExit("ERROR: patched jar is faulty (%s)" % bad)
    print("patched jar written: %s (%d entries)" % (dst, len(check.infolist())))


if __name__ == "__main__":
    main()
