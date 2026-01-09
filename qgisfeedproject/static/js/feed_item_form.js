// Capture user input in real-time
let titleField = document.getElementById("id_title");
let contentField = document.getElementById("id_content");
let imageField = document.getElementById("id_image");
let urlField = document.getElementById("id_url");
let stickyField = document.getElementById("id_sticky");
let sortingField = document.getElementById("id_sorting");
let languageField = document.getElementById("id_language_filter");
let spatialFilterField = document.getElementById("id_spatial_filter");
let publishFromField = document.getElementById("id_publish_from");
let publishToField = document.getElementById("id_publish_to");

let contentPreview = document.getElementsByName("contentPreview");
let titlePreview = document.getElementsByName("titlePreview");
let imagePreview = document.getElementsByName("imagePreview");
let urlPreview = document.getElementsByName("urlPreview");
let stickyPreview = document.getElementsByName("stickyPreview");
let sortingPreview = document.getElementsByName("sortingPreview");
let languagePreview = document.getElementsByName("languagePreview");
let spatialFilterPreview = document.getElementsByName("spatialFilterPreview");
let publishFromPreview = document.getElementsByName("publishFromPreview");
let publishToPreview = document.getElementsByName("publishToPreview");

let imageFileName = document.getElementById("imageFileName");
let urlError = document.getElementById("urlError");
let contentError = document.getElementById("contentError");
let formConfirmationBtn = document.getElementsByName("formConfirmationBtn");

let fields = [
  titleField,
  contentField,
  imageField,
  urlField,
  sortingField,
  languageField,
  spatialFilterField,
  publishFromField,
  publishToField,
];

// Update title in preview when input change
titleField.addEventListener("input", function () {
  let fieldValue = titleField.value;
  titlePreview.forEach((item) => {
    item.innerText = fieldValue;
  });
  checkFormValid();
});

// Update image in preview when input change
imageField.addEventListener("change", function () {
  let selectedImage = imageField.files[0];
  if (selectedImage) {
    let imageURL = URL.createObjectURL(selectedImage);
    let img = new Image();
    img.onload = function () {
      // Target dimensions
      const targetWidth = 500;
      const targetHeight = 354;
      const targetAspect = targetWidth / targetHeight;
      
      // Source dimensions
      const sourceAspect = img.width / img.height;
      
      // Calculate crop dimensions
      let cropWidth, cropHeight, cropX, cropY;
      
      if (sourceAspect > targetAspect) {
        // Image is wider, crop width
        cropHeight = img.height;
        cropWidth = img.height * targetAspect;
        cropX = (img.width - cropWidth) / 2;
        cropY = 0;
      } else {
        // Image is taller, crop height
        cropWidth = img.width;
        cropHeight = img.width / targetAspect;
        cropX = 0;
        cropY = (img.height - cropHeight) / 2;
      }
      
      // Create canvas and crop/scale image
      const canvas = document.createElement('canvas');
      canvas.width = targetWidth;
      canvas.height = targetHeight;
      const ctx = canvas.getContext('2d');
      
      ctx.drawImage(
        img,
        cropX, cropY, cropWidth, cropHeight,
        0, 0, targetWidth, targetHeight
      );
      
      // Convert to data URL and display
      const croppedImageURL = canvas.toDataURL('image/jpeg', 0.9);
      imagePreview.forEach((item) => {
        item.innerHTML =
          '<img src="' + croppedImageURL + '" style="max-width:100%;height:auto;border-radius:20px;">';
      });
      
      URL.revokeObjectURL(imageURL);
    };
    img.src = imageURL;
    imageFileName.innerHTML = selectedImage.name;
  } else {
    imagePreview.forEach((item) => {
      item.innerHTML = "";
    });
    imageFileName.innerHTML =
      "<i>No image chosen. Click here to add an image.</i>";
  }
  checkFormValid();
});

// Update URL in preview when input change
urlField.addEventListener("input", function () {
  urlPreview.forEach((item) => {
    item.innerHTML = urlField.value
      ? '<a href="' +
        urlField.value +
        '" target="_blank">' +
        urlField.value +
        "</a>"
      : "<i>-</i>";
  });
  checkFormValid();
});

// Update sticky in preview when change
if (stickyField) {
  stickyField.addEventListener("change", function () {
    stickyPreview.forEach((item) => {
      item.innerHTML = stickyField.checked
        ? '<span class="icon has-text-success">' +
          '<i class="fa-solid fa-circle-check"></i>' +
          "</span>"
        : '<span class="icon has-text-danger">' +
          '<i class="fa-solid fa-circle-xmark"></i>' +
          "</span>";
    });
    checkFormValid();
  });
}

// Update sorting in preview when input change
sortingField.addEventListener("input", function () {
  sortingPreview.forEach((item) => {
    item.innerText = sortingField.value ? sortingField.value : "-";
  });
  checkFormValid();
});

// Update language filter in preview when input change
languageField.addEventListener("change", function () {
  languagePreview.forEach((item) => {
    item.innerText = languageField.value
      ? // ? languageField.options[languageField.selectedIndex].text
        languageField.value
      : "-";
  });
  checkFormValid();
});

// Update spatial filter
function refreshSpatialFilter() {
  spatialFilterPreview.forEach((item) => {
    item.classList.remove("is-success");
    item.classList.remove("is-danger");
    item.classList.add(spatialFilterField.value ? "is-success" : "is-danger");
    item.innerText = spatialFilterField.value
      ? "Spatial filter set."
      : "Spatial filter not set.";
  });
  checkFormValid();
}

function refreshDates() {
  publishFromPreview.forEach((item) => {
    item.innerText = publishFromField.value
      ? moment(new Date(publishFromField.value)).format('ddd DD MMM YYYY HH:mm:ss [UTC]')
      : "-";
  });
  publishToPreview.forEach((item) => {
    item.innerText = publishToField.value
      ? moment(new Date(publishToField.value)).format('ddd DD MMM YYYY HH:mm:ss [UTC]')
      : "-";
  });
  checkFormValid();
}

// Update publish from in preview when input change
publishFromField.addEventListener("change", function () {
  publishFromPreview.forEach((item) => {
    item.innerText = publishFromField.value
      ? moment(new Date(publishFromField.value)).format('ddd DD MMM YYYY HH:mm:ss [UTC]')
      : "-";
  });
  checkFormValid();
});

// Update publish to in preview when input change
publishToField.addEventListener("change", function () {
  publishToPreview.forEach((item) => {
    item.innerText = publishToField.value
      ? moment(new Date(publishToField.value)).format('ddd DD MMM YYYY HH:mm:ss [UTC]')
      : "-";
  });
  checkFormValid();
});

function isURLValid(url) {
  // Regular expression to match a URL
  let isValid = true // We return this if the value is empty
  if (url != '') {
    const urlPattern = /^(https?:\/\/)[\w.-]+\.[a-z]{2,}(\/\S*)?$/i;
    isValid = urlPattern.test(url);
  }
  urlError.innerText = isValid ? "" : "This URL is not valid.";
  urlField.classList.toggle("is-success", isValid);
  urlField.classList.toggle("is-danger", !isValid);
  return isValid;
}


function checkFormValid(contentExceed=false) {
  const isURLValueValid = isURLValid(urlField.value);
  const isFormValid = fields.every((field) => {
    const isFieldRequired = field.hasAttribute("required");
    const value = field.value;
    return !isFieldRequired || value; // Field is not required or has a value
  });
  formConfirmationBtn.forEach((item) => {
    item.disabled = !isFormValid || !isURLValueValid || contentExceed
  })
}

document.addEventListener("DOMContentLoaded", () => {
  // Refresh some fieds on start
  refreshSpatialFilter();
  refreshDates();
  isURLValid(urlField.value);
  checkFormValid();
  // Functions to open and close the review modal
  function openModal($el) {
    $el.classList.add("is-active");
    refreshSpatialFilter();
  }

  function closeModal($el) {
    $el.classList.remove("is-active");
    refreshSpatialFilter();
  }

  function closeAllModals() {
    (document.querySelectorAll(".modal") || []).forEach(($modal) => {
      closeModal($modal);
      refreshSpatialFilter();
    });
  }

  // Add a click event on buttons to open the modal
  (document.querySelectorAll(".js-modal-trigger") || []).forEach(($trigger) => {
    const modal = $trigger.dataset.target;
    const $target = document.getElementById(modal);

    $trigger.addEventListener("click", () => {
      openModal($target);
    });
  });

  // Add a click event on various child elements to close the parent modal
  (
    document.querySelectorAll(
      ".modal-background, .modal-close, .modal-card-head .delete, .modal-card-foot .button"
    ) || []
  ).forEach(($close) => {
    const $target = $close.closest(".modal");

    $close.addEventListener("click", () => {
      closeModal($target);
    });
  });

  // Add a keyboard event to close all modals
  document.addEventListener("keydown", (event) => {
    if (event.code === "Escape") {
      closeAllModals();
    }
  });
});
