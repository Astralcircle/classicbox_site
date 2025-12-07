var month = new Date().getMonth();

if (month == 11) {
	document.addEventListener('DOMContentLoaded', function(){
		var script = document.createElement('script');
		script.src = 'https://cdn.jsdelivr.net/particles.js/2.0.0/particles.min.js';
		script.onload = function(){
			particlesJS("snow", {
				"particles": {
					"number": {
						"value": 10,
						"density": {
							"enable": true,
							"value_area": 800
						}
					},
					"color": {
						"value": "#ffffff"
					},
					"opacity": {
						"value": 0.7,
						"random": false,
						"anim": {
							"enable": false
						}
					},
					"size": {
						"value": 4,
						"random": true,
						"anim": {
							"enable": false
						}
					},
					"line_linked": {
						"enable": false
					},
					"move": {
						"enable": true,
						"speed": 2,
						"direction": "bottom",
						"random": true,
						"straight": false,
						"out_mode": "out",
						"bounce": false,
						"attract": {
							"enable": true,
							"rotateX": 300,
							"rotateY": 1200
						}
					}
				},
				"interactivity": {
					"events": {
						"onhover": {
							"enable": false
						},
						"onclick": {
							"enable": false
						},
						"resize": true
					}
				},
				"retina_detect": true
			});
		}
		document.head.append(script);
	});
}