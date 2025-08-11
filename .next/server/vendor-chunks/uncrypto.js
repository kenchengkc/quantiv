"use strict";
/*
 * ATTENTION: An "eval-source-map" devtool has been used.
 * This devtool is neither made for production nor for readable output files.
 * It uses "eval()" calls to create a separate source file with attached SourceMaps in the browser devtools.
 * If you are trying to read the output file, select a different devtool (https://webpack.js.org/configuration/devtool/)
 * or disable the default devtool with "devtool: false".
 * If you are looking for production-ready output files, see mode: "production" (https://webpack.js.org/configuration/mode/).
 */
exports.id = "vendor-chunks/uncrypto";
exports.ids = ["vendor-chunks/uncrypto"];
exports.modules = {

/***/ "(rsc)/./node_modules/uncrypto/dist/crypto.node.mjs":
/*!****************************************************!*\
  !*** ./node_modules/uncrypto/dist/crypto.node.mjs ***!
  \****************************************************/
/***/ ((__unused_webpack___webpack_module__, __webpack_exports__, __webpack_require__) => {

eval("__webpack_require__.r(__webpack_exports__);\n/* harmony export */ __webpack_require__.d(__webpack_exports__, {\n/* harmony export */   \"default\": () => (/* binding */ _crypto),\n/* harmony export */   getRandomValues: () => (/* binding */ getRandomValues),\n/* harmony export */   randomUUID: () => (/* binding */ randomUUID),\n/* harmony export */   subtle: () => (/* binding */ subtle)\n/* harmony export */ });\n/* harmony import */ var node_crypto__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! node:crypto */ \"node:crypto\");\n\n\nconst subtle = node_crypto__WEBPACK_IMPORTED_MODULE_0__.webcrypto?.subtle || {};\nconst randomUUID = () => {\n  return node_crypto__WEBPACK_IMPORTED_MODULE_0__.randomUUID();\n};\nconst getRandomValues = (array) => {\n  return node_crypto__WEBPACK_IMPORTED_MODULE_0__.webcrypto.getRandomValues(array);\n};\nconst _crypto = {\n  randomUUID,\n  getRandomValues,\n  subtle\n};\n\n\n//# sourceURL=[module]\n//# sourceMappingURL=data:application/json;charset=utf-8;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiKHJzYykvLi9ub2RlX21vZHVsZXMvdW5jcnlwdG8vZGlzdC9jcnlwdG8ubm9kZS5tanMiLCJtYXBwaW5ncyI6Ijs7Ozs7Ozs7QUFBcUM7O0FBRXJDLGVBQWUsa0RBQW9CO0FBQ25DO0FBQ0EsU0FBUyxtREFBcUI7QUFDOUI7QUFDQTtBQUNBLFNBQVMsa0RBQW9CO0FBQzdCO0FBQ0E7QUFDQTtBQUNBO0FBQ0E7QUFDQTs7QUFFbUUiLCJzb3VyY2VzIjpbIndlYnBhY2s6Ly9xdWFudGl2Ly4vbm9kZV9tb2R1bGVzL3VuY3J5cHRvL2Rpc3QvY3J5cHRvLm5vZGUubWpzPzM1OTYiXSwic291cmNlc0NvbnRlbnQiOlsiaW1wb3J0IG5vZGVDcnlwdG8gZnJvbSAnbm9kZTpjcnlwdG8nO1xuXG5jb25zdCBzdWJ0bGUgPSBub2RlQ3J5cHRvLndlYmNyeXB0bz8uc3VidGxlIHx8IHt9O1xuY29uc3QgcmFuZG9tVVVJRCA9ICgpID0+IHtcbiAgcmV0dXJuIG5vZGVDcnlwdG8ucmFuZG9tVVVJRCgpO1xufTtcbmNvbnN0IGdldFJhbmRvbVZhbHVlcyA9IChhcnJheSkgPT4ge1xuICByZXR1cm4gbm9kZUNyeXB0by53ZWJjcnlwdG8uZ2V0UmFuZG9tVmFsdWVzKGFycmF5KTtcbn07XG5jb25zdCBfY3J5cHRvID0ge1xuICByYW5kb21VVUlELFxuICBnZXRSYW5kb21WYWx1ZXMsXG4gIHN1YnRsZVxufTtcblxuZXhwb3J0IHsgX2NyeXB0byBhcyBkZWZhdWx0LCBnZXRSYW5kb21WYWx1ZXMsIHJhbmRvbVVVSUQsIHN1YnRsZSB9O1xuIl0sIm5hbWVzIjpbXSwic291cmNlUm9vdCI6IiJ9\n//# sourceURL=webpack-internal:///(rsc)/./node_modules/uncrypto/dist/crypto.node.mjs\n");

/***/ })

};
;