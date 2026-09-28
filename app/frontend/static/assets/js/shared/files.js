
let selected_row = null;
let move = false;
let copy = false;
let move_copy_source = [];
let move_copy_target = "";
let modified_time = 1.5;
let recent_response = {}
let start_index;
const LOADING_TABLE = `<tr class="skeleton-row">
                                    <td>
                                        <div class="skeleton-line" style="width: 60%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                </tr>
                                <tr class="skeleton-row">
                                    <td>
                                        <div class="skeleton-line" style="width: 60%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                </tr>
                                <tr class="skeleton-row">
                                    <td>
                                        <div class="skeleton-line" style="width: 60%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                </tr>
                                <tr class="skeleton-row">
                                    <td>
                                        <div class="skeleton-line" style="width: 60%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                </tr>
                                <tr class="skeleton-row">
                                    <td>
                                        <div class="skeleton-line" style="width: 60%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                </tr>
                                <tr class="skeleton-row">
                                    <td>
                                        <div class="skeleton-line" style="width: 60%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 30%;"></div>
                                    </td>
                                    <td>
                                        <div class="skeleton-line" style="width: 20%;"></div>
                                    </td>
                                </tr>`
///////////////////////////////////////////////////////////////////////////////////////
//LOADING FILES FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////
async function getTreeView(path) {
    const token = getCookie("_xsrf");
    start_index = 0;
    if (!move && !copy) {
        default_nav();
    }
    let body = { page: "files", path: path }
    if ($("#table-nav").attr("data-cur-path") === path) {
        body = { page: "files", path: path, modified_epoch: modified_time }
        // Only send epoch if we're requesting the same dir
    }
    $("#files-table-body").html(LOADING_TABLE);
    let res = await fetch(`/api/v2/servers/${serverId}/files`, {
        method: "POST",
        headers: {
            "X-XSRFToken": token,
        },
        body: JSON.stringify(body),
    });
    if (res.status === 304) {
        console.log("Already up to date!")
        process_tree_response(recent_response);
        return;
    }
    let responseData = await res.json();
    recent_response = responseData;
    if (responseData.status === "ok") {
        modified_time = responseData.data.root_path.modified;
        process_tree_response(responseData);
    } else {
        bootbox.alert({
            title: responseData.error,
            message: responseData.error_data
        });
    }
}

function fileIcon(value) {
    if (value.dir) return '<i class="ph-fill ph-folder-simple text-info"></i>';
    if (value.can_open) return '<i class="ph ph-file text-success"></i>';
    return '<i class="ph ph-file text-primary"></i>';
}

function setup_table_nav(response) {
    let path = response.data.root_path.local_path;
    path = path.split("\\").join("/"); //Remove \ marks
    let path_list = path.split("/");
    const container = document.querySelector("#table-nav"); // your container
    $(container).html("") // clear previous content
    $(container).attr("data-cur-path", path);

    const span = document.createElement("span");
    span.className = "tree-nav";
    let local_path = ""
    span.dataset.path = local_path; // or set the actual path if needed
    span.innerHTML = `<i class="ph-fill ph-hard-drives"></i>${path_list[0] === "" ? '&nbsp; <i class="ph-fill ph-arrow-clockwise"></i>' : ""}`; //Set root text as server icon
    container.appendChild(span);
    for (let [index, part] of path_list.entries()) {
        if (!(part === "" && index === 0)) {
            container.appendChild(document.createTextNode(" > "));
            // Create the span
            const span = document.createElement("span");
            const refresh = document.createElement("span");
            span.className = "tree-nav";
            const previous = path_list.slice(0, index);
            if (index === 0) {
                local_path = previous.join("/") + part
            } else {
                local_path = previous.join("/") + "/" + part
            }
            span.dataset.path = local_path; // or set the actual path if needed // if we're on the first iteration and it's the server ID ignore it
            span.textContent = part; // safe text;
            if (index == path_list.length - 1) {
                refresh.innerHTML = `&nbsp; <i class="ph-fill ph-arrow-clockwise"></i>`;
                span.appendChild(refresh);
            }


            // Append the span
            container.appendChild(span);
        }
    }
}

function get_more_files() {
    $("#get_more_container").remove();
    setup_table_body(recent_response);
}

function setup_table_body(response) {
    const tbody = document.querySelector("tbody");
    const response_entries = Object.entries(response.data)
    let end_index = start_index + 500;
    if (response_entries.length < end_index) {
        end_index = response_entries.length
    }
    for (let i = start_index; i < end_index; i++) {
        let [key, value] = response_entries[i];
        if (key === "root_path") continue;

        const $tr = $("<tr>")
            .addClass(value.dir ? "directory" : "file")
            .attr("data-path", value.path)
            .attr("data-can_open", value.can_open);

        const $check_column = $("<td>").append($("<div>").append($("<input>").attr("type", "checkbox").addClass("row-select").attr("data-name", key)).addClass("custom-check").addClass("checkbox-lg")).addClass("justify-content-center");

        // Column 1: icon + filename
        const $name_column = $("<td>")
            .addClass("column-1")
            .attr("data-name", key)
            .append($("<span>").html(fileIcon(value)))
            .append("\u00A0\u00A0\u00A0")
            .append(document.createTextNode(key));

        // Column 2: MIME or "Dir"
        const $type_column = $("<td>");
        if (value.mime || value.dir) {
            $type_column.text(value.mime ? value.mime : "Dir");
        } else {
            $type_column.html('<i class="ph ph-question" aria-hidden="true"></i>');
        }

        // Column 3: modified date
        const $modified_column = $("<td>").text(value.modified);

        // Column 4: size
        const $size_column = $("<td>").text(value.size || "-");

        const $can_read = $(value.permissions.can_read ? '<i class="ph ph-eyeglasses mr-2"></i>' : "");

        const $can_write = $(value.permissions.can_write ? '<i class="ph ph-floppy-disk mr-2"></i>' : "");

        const $can_execute = $(value.permissions.can_execute ? '<i class="ph ph-binary mr-2"></i>' : "");

        const $perms_column = $("<td>").append($can_read, $can_write, $can_execute)

        // Column 5: context button
        const $options_column = $("<td>")
            .addClass("context-button").append($("<span>").addClass("options").html(`<i class="ph-bold ph-dots-three options"></i>`)).addClass("text-align-center");
        if ($("#files_table thead tr:first th:visible").length > 1) {

            // Append all columns to the row
            $tr.append($check_column, $name_column, $type_column, $modified_column, $size_column, $perms_column, $options_column);
        } else {
            $tr.append($name_column)
        }

        // Append row to tbody (also as jQuery object)
        $(tbody).append($tr);
    };
    start_index = end_index
    if (response_entries.length > end_index) {
        const $tr = $("<tr>").attr("id", "get_more_container").addClass("text-align-center").append($("<td>").attr("colspan", 6).text($("#table-nav-container").attr("data-load-more")).attr("id", "get_more"));
        $(tbody).append($tr);

        $("#get_more").on("click", function () {
            get_more_files();
        });

    }
}

function setup_table_listeners() {
    $(".directory").on("mousedown", function (e) {
        if (e.button == 2) {
            return; //Dump out on right click. Let ctx pick it up
        }
        e.preventDefault();
        if (e.button == 1) {
            window.open(`/panel/server_detail?id=${serverId}&dir=${encodeURIComponent($(this).attr("data-path"))}&subpage=files#context-container`, "_blank");
            return; // Middle click new tab
        }

        // Prevent the click from firing if it’s on the context menu button
        if ($(e.target).closest(".context-button").length) return;
        if ($(e.target).closest(".row-select").length) return;
        if ($(this).children(".column-1").hasClass("editing")) return;
        if (e.ctrlKey) {
            window.open(`/panel/server_detail?id=${serverId}&dir=${encodeURIComponent($(this).attr("data-path"))}&subpage=files#context-container`, "_blank");
            return;
        }
        getTreeView($(this).attr("data-path"))
    });
    $(".file").click(function (e) {
        // Prevent the click from firing if it’s on the context menu button
        if ($(e.target).closest(".context-button").length) return;
        if ($(e.target).closest(".row-select").length) return;
        if ($(this).children(".column-1").hasClass("editing")) return;
        window.open(`/panel/edit_file?server_id=${serverId}&file=${encodeURI($(this).attr("data-path"))}`, "_blank")
    });
    $(".tree-nav").click(function (e) {
        // Prevent the click from firing if it’s on the context menu button
        if ($(e.target).closest(".context-button").length) return;
        getTreeView($(this).attr("data-path"))
    });
    setup_row_select_listener();
}

function process_tree_response(response) {
    setup_table_nav(response);
    update_copy_move_nav();
    $("#files-table-body").html("");
    setup_table_body(response);
    setup_table_listeners();
    location.hash = "";
    location.hash = "context-container"
}

function loadMenuContent(tr) {
    const ctxMenuItems = ["rename", "unzip", "download", "copy", "move", "delete"];
    const menu = $("#context-menu");
    menu.empty(); // clear previous content
    const path = $(tr).attr("data-path")
    console.log(path)
    selected_row = tr
    let zipFile = false;
    if (path) {
        zipFile = String(path).endsWith(".zip");
    }
    for (const arr_item of ctxMenuItems) {
        if (arr_item === "unzip" && !zipFile) {
            continue;
        }
        const itemContainer = $("<div>").addClass("menu-item").attr("id", arr_item);
        const item = $("<h6>").html(`<span class="${arr_item}-btn">${$("#files_table").data(arr_item)}</span>`);
        itemContainer.append(item);
        menu.append(itemContainer);
    }
    $("<input>").val()
    add_rename_listener();
    add_delete_listener();
    add_download_listener();
    add_unzip_listener();
    add_move_listener();
    add_copy_listener();

}

///////////////////////////////////////////////////////////////////////////////////////
//RENAME FILES/DIRECTORIES FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////
async function renameItem(path, name) {
    console.log("rename")
    const token = getCookie("_xsrf");
    let res = await fetch(`/api/v2/servers/${serverId}/files/create/`, {
        method: "PATCH",
        headers: {
            "X-XSRFToken": token,
        },
        body: JSON.stringify({ path: path, new_name: name }),
    });
    let responseData = await res.json();
    if (responseData.status === "ok") {
        console.log("sent ok")
        return true;
    } else {
        bootbox.alert({
            title: responseData.error,
            message: responseData.error_data
        });
        return false;
    }
}

function add_rename_listener() {
    $("#rename").on("click", function () {
        const name = $(selected_row).children(".column-1").attr("data-name");
        bootbox.prompt({
            title: $("#table-nav-container").attr("data-renameItemQuestion"),
            value: name,
            callback: async function (result) {
                if (!result) return;
                if ($(selected_row).children(".column-1").attr("data-name") != result) {
                    let rename = await renameItem($(selected_row).attr("data-path"), result);
                    if (rename === true) {
                        $(selected_row).attr("data-path", $(selected_row).attr("data-path").replace($(selected_row).children(".column-1").attr("data-name"), result))

                        $(selected_row).children(".column-1").empty()
                        let icon = '<i class="ph-fill ph-file-x text-danger"></i>'
                        if ($(selected_row).hasClass("directory")) icon = '<i class="ph-fill ph-folder-simple text-info"></i>';
                        if ($(selected_row).hasClass("file") && $(selected_row).data("can_open")) icon = '<i class="ph ph-file text-success"></i>';
                        $(selected_row).children(".column-1").append($("<span>").html(icon))
                            .append("\u00A0\u00A0\u00A0")
                            .append(document.createTextNode(result));
                        $(selected_row).children(".column-1").attr("data-name", result)
                    }
                }
            },
        });
    });
}

///////////////////////////////////////////////////////////////////////////////////////
//CREATE FILES/DIRECTORIES FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////
function setup_nav_listeners() {
    $("#create-dir").unbind("click").on("click", function () {
        bootbox.prompt(
            $("#table-nav-container").attr("data-createDirQuestion"),
            function (result) {
                if (!result) return;
                const cur_dir = $("#table-nav").attr("data-cur-path");
                create(cur_dir, result, true);
            }
        );
    });

    $("#create-file").unbind("click").on("click", function () {
        bootbox.prompt(
            $("#table-nav-container").attr("data-createFileQuestion"),
            function (result) {
                if (!result) return;
                const cur_dir = $("#table-nav").attr("data-cur-path");
                create(cur_dir, result, false);
            }
        );
    });

    $("#upload-file").unbind("click").on("click", async function uploadFilesE(event) {
        console.log(event)
        const path = $("#table-nav").attr("data-cur-path");
        let uploadHtml =
            "<div>" +
            '<form id="upload-file-form"  enctype="multipart/form-data">' +
            "<label class='upload-area' style='width:100%;text-align:center;' for='files'>" +
            "<i class='ph-bold ph-cloud-arrow-up' scale='scale: 3;'></i>" +
            "<br />" +
            $("#table-nav-container").attr("data-clickUpload") +
            "<input style='margin-left: 21%;' id='files' name='files' type='file' multiple='true'>" +
            "</label></form>" +
            "<br />" +
            "<ul style='margin-left:5px !important;' id='fileList'></ul>" +
            "</div><div class='clearfix'></div>";
        bootbox.dialog({
            message: uploadHtml,
            title: `${$("#table-nav-container").attr("data-uploadTitle")} ${path}`,
            buttons: {
                success: {
                    label: $("#table-nav-container").attr("data-upload"),
                    className: "btn-default",
                    callback: async function () {
                        if ($("#files").get(0).files.length === 0) {
                            return hideUploadBox();
                        }

                        let files = document.getElementById("files");
                        handleUpload(files.files, path);
                    },
                },
            },
        });
    });
}

function setup_select_nav() {
    if ($('.row-select:checked').length > 0) {
        move = false;
        copy = false;
        const container = $("#table-nav-buttons");
        const delete_button = $("<button>").attr("id", "delete-files").addClass("btn").addClass("btn-danger").text($("#files_table").attr("data-delete"));
        const move_button = $("<button>").attr("id", "move-files").addClass("btn").addClass("btn-info").text($("#files_table").attr("data-move"));
        const copy_button = $("<button>").attr("id", "copy-files").addClass("btn").addClass("btn-info").text($("#files_table").attr("data-copy"));
        const nbsp = "&nbsp;&nbsp;"
        container.html("")
        container.append(nbsp);
        container.append(delete_button);
        container.append(nbsp);
        container.append(move_button);
        container.append(nbsp);
        container.append(copy_button);
        $("#delete-files").on("click", function () {
            let selected_rows = $(".row-select:checked");
            bootbox.confirm({
                size: "",
                title: `${$("#table-nav-container").attr("data-deleteItemQuestion")} ${selected_rows.length} 📁!`,
                closeButton: false,
                message: $("#table-nav-container").attr("data-deleteItemQuestionMessage"),
                buttons: {
                    confirm: {
                        label: $("#table-nav-container").attr(""),
                        className: "btn-danger",
                    },
                    cancel: {
                        label: $("#table-nav-container").attr(""),
                        className: "btn-link",
                    },
                },
                callback: function (result) {
                    if (!result) return;
                    const items_to_delete = selected_rows.map(function () {
                        const $row = $(this).closest("tr");
                        const path = $row.data("path");
                        return {
                            element: $row[0],
                            path: path
                        };
                    }).get();
                    deleteItem(items_to_delete);
                    default_nav();
                },
            });
        });
        $("#move-files").on("click", function () {
            $(".row-select").prop("disabled", true);
            $(".root-select").prop("disabled", true);
            move = true;
            copy = false;
            move_copy_source = []
            let selected_rows = $(".row-select:checked");
            move_copy_source = selected_rows.map(function () {
                const path = $(this).closest("tr").data("path");
                return path;
            }).get();
            console.log(move_copy_source);
            setup_copy_move_table_nav();

        });
        $("#copy-files").on("click", function () {
            $(".row-select").prop("disabled", true);
            $(".root-select").prop("disabled", true);
            move = false;
            copy = true;
            move_copy_source = []
            let selected_rows = $(".row-select:checked");
            move_copy_source = selected_rows.map(function () {
                const path = $(this).closest("tr").data("path");
                return path;
            }).get();
            console.log(move_copy_source);
            setup_copy_move_table_nav();

        });
    } else {
        default_nav();
    }
}

async function create(parent, name, dir = false) {
    const token = getCookie("_xsrf");
    let res = await fetch(`/api/v2/servers/${serverId}/files/create/`, {
        method: "PUT",
        headers: {
            "X-XSRFToken": token,
        },
        body: JSON.stringify({ parent: parent, name: name, directory: dir }),
    });
    let responseData = await res.json();
    if (responseData.status === "ok") {
        const cur_dir = $("#table-nav").attr("data-cur-path");
        getTreeView(cur_dir);
    } else {
        bootbox.alert({
            title: responseData.error,
            message: responseData.error_data
        });
    }
}
///////////////////////////////////////////////////////////////////////////////////////
//DELETE FILES/DIRECTORIES FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////
function add_delete_listener() {
    $("#delete").on("click", function () {
        const path = $(selected_row).attr("data-path");
        bootbox.confirm({
            size: "",
            title: `${$("#table-nav-container").attr("data-deleteItemQuestion")} ${path}!`,
            closeButton: false,
            message: $("#table-nav-container").attr("data-deleteItemQuestionMessage"),
            buttons: {
                confirm: {
                    label: $("#table-nav-container").attr(""),
                    className: "btn-danger",
                },
                cancel: {
                    label: $("#table-nav-container").attr(""),
                    className: "btn-link",
                },
            },
            callback: function (result) {
                if (!result) return;
                deleteItem([{ "element": selected_row, "path": path }]);
            },
        });
    });
}


async function deleteItem(items) {
    const token = getCookie("_xsrf");
    let items_to_delete = []
    for (let item of items) {
        items_to_delete.push({ "filename": String(item["path"]) })
    }
    console.log(items_to_delete)
    let res = await fetch(`/api/v2/servers/${serverId}/files`, {
        method: "DELETE",
        headers: {
            "X-XSRFToken": token,
        },
        body: JSON.stringify({ "file_system_objects": items_to_delete }),
    });
    let responseData = await res.json();
    if (responseData.status === "ok") { // Delete the row dom objects
        for (let item of items) {
            $(item["element"]).remove()
        }
    } else {
        bootbox.alert({
            title: responseData.error,
            message: responseData.error_data
        });
    }
}

///////////////////////////////////////////////////////////////////////////////////////
//DOWNLOAD FILES/DIRECTORIES FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////
function add_download_listener() {
    $("#download").on("click", function () {
        const path = $(selected_row).attr("data-path");
        window.open(`/api/v2/servers/${serverId}/files/${encodeURIComponent(path)}/download`, '_blank');
    });
}

function add_unzip_listener() {
    $("#unzip").on("click", async function () {
        const path = $(selected_row).attr("data-path");
        const unzip_id = uuidv4()
        const name = $(selected_row).children(".column-1").attr("data-name");
        const unzip_progress = `      
        <div style="width: 100%; min-width: 100%;" id="upload-progress-bar-${unzip_id}-container">
          <small>${name}:</small>
          <br>
        <div class="d-flex">
          <span class="upload-percent" id="upload-percent-${unzip_id}"></span>
          <div
              id="upload-progress-bar-${unzip_id}"
              class="progress-bar progress-bar-striped progress-bar-animated files-progress bg-warning"
              role="progressbar"
              style="width: 100%; height: 10px;"
              aria-valuenow="0"
              aria-valuemin="0"
              aria-valuemax="100"
          ></div>
          </div>
      </div>`
        let res = await fetch(`/api/v2/servers/${serverId}/files/zip/`, {
            method: "POST",
            headers: {
                "X-XSRFToken": token,
            },
            body: JSON.stringify({ folder: path, proc_id: unzip_id }),
        });
        let responseData = await res.json();
        if (responseData.status === "ok") {
            $("#upload-progress-bar-parent").append(unzip_progress);
        } else {
            bootbox.alert({
                title: responseData.error,
                message: responseData.error_data
            });
        }
    });
}

///////////////////////////////////////////////////////////////////////////////////////
//UPLOAD FILES FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////
$(document).ready(function () {
    //DROPZONE INITIALIZATION
    $('#status').collapse({
        toggle: true
    })
    const $dropZone = $("#drop-zone");

    $dropZone.on("dragover", function (e) {
        e.preventDefault();
        e.stopPropagation();
        $dropZone.addClass("drop-hover");
    });

    $dropZone.on("dragleave", function (e) {
        e.preventDefault();
        e.stopPropagation();
        $dropZone.removeClass("drop-hover");
    });

    $dropZone.on("drop", function (e) {
        e.preventDefault();
        e.stopPropagation();
        $dropZone.removeClass("drop-hover");

        const files = e.originalEvent.dataTransfer.files;
        if (files.length === 0) return;

        handleUpload(files, $("#table-nav").attr("data-cur-path"));
    });

});

function uuidv4() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replaceAll(/[xy]/g, function (c) {
        const r = Math.trunc(Math.random() * 16),
            v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

async function handleUpload(files, path) {

    let nFiles = files.length;
    const uploadPromises = [];
    for (let i = 0; i < nFiles; i++) {
        let file_id = uuidv4();
        const file = files[i];
        const progressHtml = `
      <div style="width: 100%; min-width: 100%;" id="upload-progress-bar-${file_id}-container">
          <small>${file.name}:</small>
          <br>
          <div class="d-flex">
          <span class="upload-percent" id="upload-percent-${file_id}"></span>
          <div
              id="upload-progress-bar-${file_id}"
              class="progress-bar progress-bar-striped progress-bar-animated files-progress"
              role="progressbar"
              style="width: 100%; height: 10px;"
              aria-valuenow="0"
              aria-valuemin="0"
              aria-valuemax="100"
          ></div>
          </div>
      </div>
      `;

        $("#upload-progress-bar-parent").append(progressHtml);

        const uploadPromise = uploadFile(
            "server_upload",
            file,
            path,
            i,
            file_id,
            (progress) => {
                $(`#upload-progress-bar-${i + 1}`).attr(
                    "aria-valuenow",
                    progress
                );
                $(`#upload-progress-bar-${i + 1}`).css(
                    "width",
                    progress + "%"
                );
            }
        );
        uploadPromises.push(uploadPromise);
    }

    await Promise.all(uploadPromises);
}

async function calculateFileHash(file) {
    const arrayBuffer = await file.arrayBuffer();
    const hashBuffer = await crypto.subtle.digest("SHA-256", arrayBuffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const hashHex = hashArray
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");

    return hashHex;
}

///////////////////////////////////////////////////////////////////////////////////////
//MOVE/COPY FILES FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////
function add_move_listener() {
    $("#move").on("click", function () {
        move = true;
        copy = false;
        move_copy_source = []
        move_copy_source.push($(selected_row).attr("data-path"));
        setup_copy_move_table_nav();
    });
}
function add_copy_listener() {
    $("#copy").on("click", function () {
        move = false;
        copy = true;
        move_copy_source = []
        move_copy_source.push($(selected_row).attr("data-path"));
        setup_copy_move_table_nav();
    });
}

function setup_copy_move_table_nav() {
    const container = $("#table-nav-buttons");
    container.html("");
    const nbsp = "&nbsp;&nbsp;&nbsp;";
    let move_button = $("<button>").attr("id", "move-op").addClass("btn").addClass("btn-info").text($("#files_table").attr("data-move"));
    if (copy) {
        move_button = $("<button>").attr("id", "copy-op").addClass("btn").addClass("btn-info").text($("#files_table").attr("data-copy"));
    }
    let move_source = $("<span>").attr("id", "copy-move-source").html(`${move_copy_source.length} <i class="ph ph-file"></i>`);
    if (move_copy_source.length <= 1) {
        move_source = $("<span>").attr("id", "copy-move-source").text(move_copy_source[0].replace(serverId, ""));
    }
    const move_target = $("<span>").attr("id", "copy-move-target");
    const move_arrow = $("<span>").attr("id", "copy-move-arrow").html(`<i class="ph-bold ph-arrow-right"></i>`);
    const cancel = $("<button>").attr("id", "copy-move-cancel").addClass("btn").addClass("btn-secondary").text($("#files_table").attr("data-cancel"));
    container.html("");
    container.append(move_source);
    container.append(nbsp);
    container.append(move_arrow)
    container.append(nbsp);
    container.append(move_target);
    container.append(nbsp);
    container.append(nbsp);
    container.append(move_button);
    container.append(nbsp);
    container.append(cancel);
    setup_move_listener();
}

function update_copy_move_nav() {

    move_copy_target = $("#table-nav").attr("data-cur-path");
    $("#copy-move-target").text(move_copy_target.replace(serverId, "/"));

}


function default_nav() {
    move = false;
    copy = false;
    const container = $("#table-nav-buttons");
    const createDir = $("<button>").attr("id", "create-dir").addClass("btn").addClass("btn-info").text($("#table-nav-buttons").attr("data-dir"));
    const createFile = $("<button>").attr("id", "create-file").addClass("btn").addClass("btn-info").text($("#table-nav-buttons").attr("data-file"));
    const upload = $("<button>").attr("id", "upload-file").addClass("btn").addClass("btn-info").text($("#table-nav-buttons").attr("data-upload"));

    container.html("")
    container.append(createDir);
    container.append("&nbsp;&nbsp;");
    container.append(createFile)
    container.append("&nbsp;&nbsp;");
    container.append(upload);
    setup_nav_listeners();
}

function setup_move_listener() {
    $("#copy-move-cancel").on("click", function () {
        $(".row-select").prop("disabled", false).prop("checked", false);
        $(".root-select").prop("disabled", false).prop("checked", false);
        const checkboxes = document.querySelectorAll('.row-select');
        const main_checked = $(this).prop("checked");
        for (const row of checkboxes) {
            $(row).prop("checked", main_checked).trigger("change");
        }
        default_nav();
    });
    if (copy) {
        $("#copy-op").on("click", async function () {
            $(".row-select").prop("disabled", false).prop("checked", false);
            $(".root-select").prop("disabled", false).prop("checked", false);
            const cur_dir = $("#table-nav").attr("data-cur-path");
            const copy_of_move_copy_source = move_copy_source //copy this off just in case someone is too quick
            const copy_of_move_copy_target = move_copy_target
            let send_items = []
            for (let item of copy_of_move_copy_source) {
                send_items.push({ "source_path": item, "target_path": copy_of_move_copy_target })
            }
            let res = await fetch(`/api/v2/servers/${serverId}/files/copy/`, {
                method: "POST",
                headers: {
                    "X-XSRFToken": token,
                },
                body: JSON.stringify({ "file_system_objects": send_items }),
            });
            let responseData = await res.json();
            if (responseData.status === "ok") {
                getTreeView(cur_dir);
                default_nav();
            } else {
                bootbox.alert({
                    title: responseData.error,
                    message: responseData.error_data
                });
            }
        });
    } else {
        $("#move-op").on("click", async function () {
            $(".row-select").prop("disabled", false).prop("checked", false);
            $(".root-select").prop("disabled", false).prop("checked", false);
            const cur_dir = $("#table-nav").attr("data-cur-path");
            const copy_of_move_copy_source = move_copy_source //copy this off just in case someone is too quick
            const copy_of_move_copy_target = move_copy_target
            let send_items = []
            for (let item of copy_of_move_copy_source) {
                send_items.push({ "source_path": item, "target_path": copy_of_move_copy_target })
            }
            console.log(send_items)
            let res = await fetch(`/api/v2/servers/${serverId}/files/move/`, {
                method: "POST",
                headers: {
                    "X-XSRFToken": token,
                },
                body: JSON.stringify({ "file_system_objects": send_items }),
            });
            let responseData = await res.json();
            if (responseData.status === "ok") {
                getTreeView(cur_dir);
                default_nav();
            } else {
                bootbox.alert({
                    title: responseData.error,
                    message: responseData.error_data
                });
            }
        });
    }
    location.hash = "";
    location.hash = "context-container"
}


///////////////////////////////////////////////////////////////////////////////////////
//PAGE FUNCTIONS
///////////////////////////////////////////////////////////////////////////////////////

$(document).ready(function () {
    setup_nav_listeners();
    $("#file-status").on("click", function () {
        $("#file-status-content").toggleClass("d-none");
        if ($("#file-status-content").hasClass("d-none")) {
            $("#status-caret").html(`<i class="ph-bold ph-caret-up"></i>`)
        } else {
            $("#status-caret").html(`<i class="ph-bold ph-caret-down"></i>`)
        }
    });
});

function setup_row_select_listener() {
    console.log('listener setup')
    $(".row-select").on("change", function () {
        if ($(this).prop("checked")) {
            $(this).closest("tr").addClass("highlight-row");
        } else {
            $(this).closest("tr").removeClass("highlight-row");
        }
        setup_select_nav();
    });
    $(".root-select").on("change", function () {
        const checkboxes = document.querySelectorAll('.row-select');
        const main_checked = $(this).prop("checked");
        for (const row of checkboxes) {
            $(row).prop("checked", main_checked).trigger("change");
        }
    });
}