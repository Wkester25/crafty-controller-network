$("#renewCodes").click(async function () {
    // userId may be defined in the outer scope of the page including this script
    const renewUserId = typeof userId === "undefined" ? $(this).data("id") : userId;
    let res = await fetch(`/api/v2/users/${renewUserId}/totp/recovery/renew/`, {
        method: 'GET',
        headers: {
            'X-XSRFToken': token
        },
    });
    let responseData = await res.json();
    if (responseData.status === "ok") {
        let backupcodes = createBackupCodesDiv(responseData.data)
        bootbox.alert(backupcodes);
        registerCopyFunction();
    } else {
        bootbox.alert(responseData.error_data);
    }
});

function createBackupCodesDiv(data) {
    let backupcodes = `<h4>${$("#createButton").data("backup-codes")}</h4>
                        <ul class="list-group">`;
    data["backup_codes"].forEach(element => {
        backupcodes += `<li class="list-group-item code">${element}</li>`;
    });
    backupcodes += `</ul>`
    backupcodes += `<button class="btn btn-info copy-codes-btn" id="copy-codes">${$("#createButton").data("copy-codes")}</button>`
    if (data["backup_codes"].length == 0) {
        backupcodes = `<div style="text-align: center; color: grey;">
                            ${$("#createButton").data("backup-codes-max")}
                            </div>`
    }

    return backupcodes
}

function registerCopyFunction() {
    $("#copy-codes").click(function () {
        let text = ``
        $(".code").each(function () {
            text += `${$(this).text()}\n`;
        });
        navigator.clipboard.writeText(text);
        const button = $(this);
        button.addClass("animate-outline");
        setTimeout(() => button.removeClass("animate-outline"), 600);
    });
}
