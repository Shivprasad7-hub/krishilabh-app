function startRecognition(fieldId) {
    const field = document.getElementById(fieldId);
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        alert("Speech Recognition not supported in this browser!");
        return;
    }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new Recognition();
    recognition.lang = "en-IN";
    recognition.start();
    recognition.onresult = function(event) {
        field.value = event.results[0][0].transcript;
    };
    recognition.onerror = function(e) {
        alert("Speech error: "+ e.error);
    };
}