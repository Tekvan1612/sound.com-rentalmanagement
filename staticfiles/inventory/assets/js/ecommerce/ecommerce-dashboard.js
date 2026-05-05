/*! For license information please see ecommerce-dashboard.js.LICENSE.txt */
"use strict";
document.addEventListener("DOMContentLoaded", (function () {
    new ProgressBar.Circle(circleprogressblue1, {
        color: "rgba(100, 150, 86, 1)",
        strokeWidth: 5,
        trailWidth: 10,
        easing: "easeInOut",
        trailColor: "rgba(100, 150, 86, 0.15)",
        duration: 1400,
        text: {autoStyleContainer: !1},
        from: {color: "rgba(100, 150, 86, 0.1)", width: 5},
        to: {color: "rgba(100, 150, 86, 1)", width: 5},
        step: function (a, r) {
            r.path.setAttribute("stroke", a.color), r.path.setAttribute("stroke-width", a.width);
            var t = Math.round(100 * r.value());
            0 === t ? r.setText("") : r.setText(t + "<small>%<small><br><small class='fs-12 opacity-50'>Ready</small>")
        }
    }).animate(.85), new ProgressBar.Circle(circleprogressgreen1, {
        color: "rgba(204, 112, 140, 1)",
        strokeWidth: 5,
        trailWidth: 10,
        easing: "easeInOut",
        trailColor: "rgba(204, 112, 140, 0.15)",
        duration: 1400,
        text: {autoStyleContainer: !1},
        from: {color: "rgba(204, 112, 140, 0.1)", width: 5},
        to: {color: "rgba(204, 112, 140, 1)", width: 5},
        step: function (a, r) {
            r.path.setAttribute("stroke", a.color), r.path.setAttribute("stroke-width", a.width);
            var t = Math.round(100 * r.value());
            0 === t ? r.setText("") : r.setText(t + "<small>%<small><br><small class='fs-12 opacity-50'>Done</small>")
        }
    }).animate(.65), window.randomScalingFactor = function () {
        return Math.round(20 * Math.random())
    };
    var a = document.getElementById("summarychart").getContext("2d"), r = a.createLinearGradient(0, 0, 0, 200);
    r.addColorStop(0, "rgba(100, 150, 86, 1)"), r.addColorStop(1, "rgba(100, 150, 86, 0.2)"), (e = a.createLinearGradient(0, 0, 0, 200)).addColorStop(0, "rgba(217, 240, 101, 1)"), e.addColorStop(1, "rgba(171, 193, 71, 0.2)");
    var t = {
        type: "bar",
        data: {
            labels: ["1 Feb", "2 Fb", "3 Feb", "5 Feb", "6 Feb", "7 Feb", "8 Feb"],
            datasets: [{
                label: "# of hours",
                data: [randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor()],
                radius: 0,
                backgroundColor: r,
                borderColor: "rgba(204, 112, 140, 1)",
                borderWidth: 0,
                fill: !0,
                tension: .5,
                borderRadius: 10
            }, {
                label: "# of hours prev",
                data: [randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor()],
                radius: 0,
                backgroundColor: e,
                borderColor: "rgba(217, 240, 101, 1)",
                borderWidth: 0,
                fill: !0,
                tension: .5,
                borderRadius: 10
            }]
        },
        options: {
            animation: !0,
            maintainAspectRatio: !1,
            plugins: {legend: {display: !1}},
            scales: {y: {display: !1, beginAtZero: !0}, x: {grid: {display: !1}, display: !1, beginAtZero: !0}}
        }
    }, o = new Chart(a, t);
    setInterval((function () {
        t.data.datasets.forEach((function (a) {
            a.data = a.data.map((function () {
                return randomScalingFactor()
            }))
        })), o.update()
    }), 3e3);
    var e, n = document.getElementById("saleschart").getContext("2d");
    (e = n.createLinearGradient(0, 0, 0, 160)).addColorStop(0, "rgba(100, 150, 86, 0.5)"), e.addColorStop(1, "rgba(210, 230, 106, 0.1)");
    var i = {
        type: "line",
        data: {
            labels: ["1 Feb", "2 Fb", "3 Feb", "5 Feb", "6 Feb", "7 Feb", "8 Feb", "9 Feb"],
            datasets: [{
                label: "# of hours",
                data: [randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor()],
                radius: 0,
                backgroundColor: e,
                borderColor: "rgba(100, 150, 86, 1)",
                borderWidth: 0,
                fill: !0,
                tension: .5,
                borderRadius: 10
            }]
        },
        options: {
            animation: !0,
            maintainAspectRatio: !1,
            plugins: {legend: {display: !1}},
            scales: {y: {display: !1, beginAtZero: !0}, x: {grid: {display: !1}, display: !0, beginAtZero: !0}}
        }
    }, l = (new Chart(n, i), document.getElementById("revenuchart").getContext("2d")), d = {
        type: "line",
        data: {
            labels: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"],
            datasets: [{
                label: "# of hours",
                data: [randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor()],
                radius: 1,
                backgroundColor: "transparent",
                borderColor: "rgba(100, 150, 86, 1)",
                borderWidth: 1,
                fill: !0,
                tension: 0,
                borderRadius: 10
            }, {
                label: "# of hours",
                data: [randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor(), randomScalingFactor()],
                radius: 1,
                backgroundColor: "transparent",
                borderColor: "rgb(190, 197, 161)",
                borderWidth: 1,
                fill: !0,
                tension: 0,
                borderRadius: 10
            }]
        },
        options: {
            animation: !0,
            maintainAspectRatio: !1,
            plugins: {legend: {display: !1}},
            scales: {y: {display: !1, beginAtZero: !0}, x: {grid: {display: !1}, display: !0, beginAtZero: !0}}
        }
    }, c = new Chart(l, d);
    setInterval((function () {
        d.data.datasets.forEach((function (a) {
            a.data = a.data.map((function () {
                return randomScalingFactor()
            }))
        })), c.update()
    }), 3e3), new ProgressBar.Circle(circleprogressblue, {
        color: "rgba(110, 119, 145, 1)",
        strokeWidth: 10,
        trailWidth: 10,
        easing: "easeInOut",
        trailColor: "rgba(110, 119, 145, 0.15)",
        duration: 1400,
        text: {autoStyleContainer: !1},
        from: {color: "rgba(110, 119, 145, 0.75)", width: 10},
        to: {color: "rgba(110, 119, 145, 1)", width: 10},
        step: function (a, r) {
            r.path.setAttribute("stroke", a.color), r.path.setAttribute("stroke-width", a.width);
            var t = Math.round(100 * r.value());
            0 === t ? r.setText("") : r.setText(t + "<small>%<small>")
        }
    }).animate(.35), new ProgressBar.Circle(circleprogressyellow, {
        color: "rgba(110, 119, 145, 1)",
        strokeWidth: 10,
        trailWidth: 10,
        easing: "easeInOut",
        trailColor: "rgba(110, 119, 145, 0.15)",
        duration: 1400,
        text: {autoStyleContainer: !1},
        from: {color: "rgba(110, 119, 145, 0.5)", width: 10},
        to: {color: "rgba(110, 119, 145, 1)", width: 10},
        step: function (a, r) {
            r.path.setAttribute("stroke", a.color), r.path.setAttribute("stroke-width", a.width);
            var t = Math.round(100 * r.value());
            0 === t ? r.setText("") : r.setText(t + "<small>%<small>")
        }
    }).animate(.15);
    var s = document.getElementById("doughnutchart").getContext("2d");
    new Chart(s, {
        type: "doughnut",
        data: {
            labels: ["Electronics", "Fashion", "Gaming", "Groceries", "Appliances"],
            datasets: [{
                label: "Age Range",
                data: [40, 10, 15, 25, 10],
                backgroundColor: ["rgba(217, 240, 101, 1)", "rgb(197, 214, 184)", "rgb(166, 194, 148)", "rgb(130, 174, 118)", "rgba(100, 150, 86, 1)"],
                borderWidth: 0
            }]
        },
        options: {
            responsive: !0,
            tooltips: {position: "nearest", yAlign: "bottom"},
            plugins: {legend: {display: !1, position: "top"}, title: {display: !1, text: "Chart.js polarArea Chart"}}
        }
    })
}));